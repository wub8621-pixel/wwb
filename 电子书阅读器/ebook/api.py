"""FastAPI 服务：把 txt → EPUB 的能力暴露成 HTTP 接口。

路由（前缀可在 server.route_prefix 配置）：
    GET  /health                         存活检查
    POST {prefix}/convert                上传一个 txt，同步返回 EPUB
    POST {prefix}/convert/batch          上传多个 txt，返回逐本结果
    GET  {prefix}/download/{run}/{file}  下载已生成的书
    GET  {prefix}/runs/{run}             查询某次运行的结果
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from config.loader import ConfigError, load_config, project_path

from .epub import safe_filename
from .graph import build_graph
from .ingest import IngestError
from .llm import make_llm
from .logging_setup import setup_logging, write_report

CHUNK_SIZE = 1024 * 1024
RUN_ID_RE = re.compile(r"^[0-9A-Za-z_-]{8,40}$")
SERVICE_NAME = "电子书阅读器"
SERVICE_VERSION = "1.0.0"


def _new_run_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"


def _unique_path(directory: Path, name: str) -> Path:
    """同一批次里重名文件加序号，避免互相覆盖。"""
    target = directory / name
    index = 2
    while target.exists():
        target = directory / f"{Path(name).stem}-{index}{Path(name).suffix}"
        index += 1
    return target


def _check_name(raw: str | None, allowed_extensions: list[str]) -> str:
    name = Path(raw or "").name
    if not name or Path(name).suffix.lower() not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"只接受 {' / '.join(allowed_extensions)} 文件，收到：{raw or '空文件名'}",
        )
    return safe_filename(Path(name).stem) + Path(name).suffix.lower()


async def _save_upload(file: UploadFile, target: Path, max_bytes: int) -> None:
    """边读边写，超过上限立即中止并清理，避免大文件占满内存。"""
    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{target.name} 超过 {max_bytes // 1024 // 1024}MB 上限",
                    )
                handle.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise


def _run_one(cfg, logger, llm, source: Path, output_dir: Path, use_llm: bool) -> dict:
    """跑一次 LangGraph；单本失败转成结果项，不中断整批。"""
    graph = build_graph(cfg, logger, llm if use_llm else None)
    try:
        state = graph.invoke(
            {"source": str(source), "output_dir": str(output_dir)},
            config={"recursion_limit": cfg.get("graph.recursion_limit")},
        )
    except IngestError as exc:
        logger.warning("处理 %s 被拒绝：%s", source.name, exc)
        return {"file": source.name, "ok": False, "error": str(exc)}
    except Exception as exc:  # 单本失败不拖垮整批
        logger.exception("处理 %s 失败", source.name)
        return {"file": source.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    output = Path(state["output_path"])
    return {
        "file": source.name,
        "ok": True,
        "chapters": len(state["chapters"]),
        "repaired": bool(state.get("repaired")),
        "encoding": state.get("encoding", ""),
        "warnings": state.get("warnings", []),
        "output": output.name,
        "size_kb": round(output.stat().st_size / 1024, 1),
    }


def _record_run(logger, log_dir: Path, run_dir: Path, payload: dict) -> None:
    """落 run.json 与 logs/reports，接口返回前就能追溯。"""
    (run_dir / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = write_report(log_dir, payload, prefix="api")
    logger.info("接口运行 %s 完成，报告：%s", payload["run_id"], report)


def create_app(cfg=None, llm="auto") -> FastAPI:
    cfg = cfg or load_config()
    logger, log_dir = setup_logging(cfg)
    runs_dir = project_path(cfg.get("server.runs_dir"))
    runs_dir.mkdir(parents=True, exist_ok=True)
    if llm == "auto":
        llm = make_llm(cfg, logger)

    prefix = cfg.get("server.route_prefix").rstrip("/")
    max_bytes = cfg.get("server.max_upload_mb") * 1024 * 1024
    max_batch = cfg.get("server.max_batch_files")
    extensions = [item.lower() for item in cfg.get("server.allowed_extensions")]

    app = FastAPI(title=SERVICE_NAME, version=SERVICE_VERSION)
    app.state.cfg = cfg
    app.state.logger = logger
    app.state.log_dir = log_dir
    app.state.runs_dir = runs_dir
    app.state.llm = llm

    def _new_run_dir() -> tuple[str, Path, Path]:
        run_id = _new_run_id()
        run_dir = runs_dir / run_id
        source_dir = run_dir / "source"
        source_dir.mkdir(parents=True)
        return run_id, run_dir, source_dir

    def _meta(run_id: str, mode: str, results: list[dict]) -> dict:
        return {
            "run_id": run_id,
            "service": SERVICE_NAME,
            "mode": mode,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "results": results,
        }

    def _error_if_empty(run_dir: Path, results: list[dict]) -> None:
        if not any(item["ok"] for item in results):
            shutil.rmtree(run_dir, ignore_errors=True)
            raise HTTPException(status_code=422, detail=results)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "model": cfg.get("model.name"),
            "llm_enabled": llm is not None,
            "routes": {
                "convert": f"{prefix}/convert",
                "batch": f"{prefix}/convert/batch",
                "download": f"{prefix}/download/{{run_id}}/{{filename}}",
                "runs": f"{prefix}/runs/{{run_id}}",
            },
        }

    @app.post(f"{prefix}/convert")
    async def convert(
        file: UploadFile = File(..., description="需要转换的 txt 文件"),
        use_llm: bool = Form(True, description="是否允许 LLM 兜底"),
    ):
        name = _check_name(file.filename, extensions)
        run_id, run_dir, source_dir = _new_run_dir()
        source = source_dir / name
        await _save_upload(file, source, max_bytes)

        result = await run_in_threadpool(
            _run_one, cfg, logger, llm, source, run_dir, use_llm
        )
        _error_if_empty(run_dir, [result])

        payload = _meta(run_id, "single", [result])
        _record_run(logger, log_dir, run_dir, payload)
        target = run_dir / result["output"]
        return FileResponse(
            target,
            media_type="application/epub+zip",
            filename=result["output"],
            headers={
                "X-Run-Id": run_id,
                "X-Chapters": str(result["chapters"]),
                "X-Repaired": "1" if result["repaired"] else "0",
            },
        )

    @app.post(f"{prefix}/convert/batch")
    async def convert_batch(
        files: list[UploadFile] = File(..., description="需要转换的 txt 文件列表"),
        use_llm: bool = Form(True, description="是否允许 LLM 兜底"),
    ) -> dict:
        if not files:
            raise HTTPException(status_code=400, detail="至少上传一个文件")
        if len(files) > max_batch:
            raise HTTPException(
                status_code=400, detail=f"一次最多 {max_batch} 个文件，收到 {len(files)} 个"
            )

        run_id, run_dir, source_dir = _new_run_dir()
        results = []
        for upload in files:
            try:
                name = _check_name(upload.filename, extensions)
            except HTTPException as exc:
                results.append({"file": upload.filename, "ok": False, "error": exc.detail})
                continue
            source = _unique_path(source_dir, name)
            try:
                await _save_upload(upload, source, max_bytes)
            except HTTPException as exc:
                results.append({"file": upload.filename, "ok": False, "error": exc.detail})
                continue
            results.append(
                await run_in_threadpool(
                    _run_one, cfg, logger, llm, source, run_dir, use_llm
                )
            )

        _error_if_empty(run_dir, results)

        for item in results:
            if item["ok"]:
                item["download_url"] = (
                    f"{prefix}/download/{run_id}/{quote(item['output'])}"
                )

        payload = _meta(run_id, "batch", results)
        _record_run(logger, log_dir, run_dir, payload)
        return payload

    @app.get(f"{prefix}/runs/{{run_id}}")
    def get_run(run_id: str) -> dict:
        run_dir = _resolve_run(runs_dir, run_id)
        payload = run_dir / "run.json"
        if not payload.is_file():
            raise HTTPException(status_code=404, detail="该运行还没有结果")
        return json.loads(payload.read_text(encoding="utf-8"))

    @app.get(f"{prefix}/download/{{run_id}}/{{filename}}")
    def download(run_id: str, filename: str) -> FileResponse:
        run_dir = _resolve_run(runs_dir, run_id)
        target = (run_dir / Path(filename).name).resolve()
        if target.parent != run_dir.resolve() or target.suffix.lower() != ".epub":
            raise HTTPException(status_code=400, detail="文件名非法")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(target, media_type="application/epub+zip", filename=target.name)

    return app


def _resolve_run(runs_dir: Path, run_id: str) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="run_id 非法")
    run_dir = (runs_dir / run_id).resolve()
    if run_dir.parent != runs_dir.resolve() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="运行不存在")
    return run_dir


def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"配置有误：{exc}")
        return 2
    uvicorn.run(
        create_app(cfg),
        host=cfg.get("server.host"),
        port=cfg.get("server.port"),
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
