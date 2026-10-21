
cat >> ~/.zshrc <<'EOF'

# DeepSeek via Claude Code
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
export ANTHROPIC_AUTH_TOKEN="sk-3de39fe2203e45448833360653cb884a"
export API_TIMEOUT_MS=1200000
export ANTHROPIC_MODEL="deepseek-chat"
export ANTHROPIC_SMALL_FAST_MODEL="deepseek-chat"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
EOF
source ~/.zshrc
