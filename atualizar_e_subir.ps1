# Atualiza os anúncios (incluindo o Imovelweb), analisa as fotos pelo Claude
# e sobe o site local.
#
#   .\atualizar_e_subir.ps1                 # tudo
#   .\atualizar_e_subir.ps1 -SemVisao       # sem análise de fotos
#   .\atualizar_e_subir.ps1 -LimiteVisao 50 # analisa no máximo 50 anúncios
#   .\atualizar_e_subir.ps1 -SoSite         # só sobe o site
#
# O tempo de cada etapa aparece no "Resumo" no fim da atualização.
param(
    [switch]$SemVisao,
    [switch]$SoSite,
    [int]$LimiteVisao = 0
)

# "Continue": o Flask e o Playwright escrevem avisos no stderr, e com "Stop" o
# PowerShell trata cada um como erro fatal e derruba o processo
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

if (-not $SoSite) {
    if (-not $SemVisao) {
        # o login do Claude é interativo: melhor descobrir agora do que
        # depois da coleta inteira
        & $py -c "from cacaimoveis.visao import verificar_login_claude as v; p = v(); print(p) if p else None; raise SystemExit(1 if p else 0)"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Claude Code sem login. Rode 'caca-login' e tente de novo." -ForegroundColor Red
            exit 1
        }
    }

    $args_ = @("-m", "cacaimoveis.atualizar", "--imovelweb", "--provedor", "claude")
    if ($SemVisao) { $args_ += @("--pular", "visao") }
    if ($LimiteVisao -gt 0) { $args_ += @("--limite-visao", $LimiteVisao) }

    & $py @args_
    # código 1 = alguma etapa falhou (as outras seguiram); o site sobe mesmo assim
    if ($LASTEXITCODE -eq 3) { Write-Host "Já há uma atualização rodando." -ForegroundColor Yellow; exit 3 }
}

Write-Host "`nSubindo o site em http://127.0.0.1:5000" -ForegroundColor Green
Start-Process "http://127.0.0.1:5000"
& $py -m cacaimoveis.webapp
