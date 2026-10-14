param(
    [ValidateSet('live','evaluate-live')]
    [string]$Mode = 'live',
    [string]$Question = '2011年11月经营情况怎么样？',
    [string]$Suite = 'evals\b2-intent-dev.json',
    [string]$KeyFile = $env:B2_DPAPI_KEY_FILE
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python -ErrorAction Stop).Source }
$previousKeyFile = $env:B2_DPAPI_KEY_FILE

if ($KeyFile) {
    $env:B2_DPAPI_KEY_FILE = $KeyFile
}
$env:B2_API_BASE_URL = 'https://api.siliconflow.cn/v1'
$env:B2_MODEL = 'deepseek-ai/DeepSeek-V4-Flash'
$env:B2_INPUT_PRICE_CNY_PER_M = '3'
$env:B2_CACHE_PRICE_CNY_PER_M = '0.3'
$env:B2_OUTPUT_PRICE_CNY_PER_M = '9'
$env:B2_TIMEOUT_SECONDS = '75'
$env:B2_MAX_OUTPUT_TOKENS = '700'

Push-Location $projectRoot
try {
    if ($Mode -eq 'live') {
        & $python -m b2 live --question $Question --target-cny 10 --budget-cny 20
    } else {
        & $python -m b2 evaluate-live --suite $Suite --target-cny 10 --budget-cny 20
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
    if ($null -eq $previousKeyFile) {
        Remove-Item Env:B2_DPAPI_KEY_FILE -ErrorAction SilentlyContinue
    } else {
        $env:B2_DPAPI_KEY_FILE = $previousKeyFile
    }
}
