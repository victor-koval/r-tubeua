# Збірка R-TubeUA в один .exe
#
# Запуск:
#   .\build.ps1                 зібрати з поточною версією
#   .\build.ps1 -Bump           підняти останнє число (1.0.0 -> 1.0.1) і зібрати
#   .\build.ps1 -UpdateYtdlp    спершу оновити yt-dlp до свіжої версії
#
# yt-dlp вшивається в .exe, тож коли YouTube щось змінить і завантаження
# перестануть працювати, лікується це перезбіркою з -UpdateYtdlp.

param(
    [switch]$Bump,
    [switch]$UpdateYtdlp
)

# НЕ ставимо ErrorActionPreference = "Stop": PyInstaller пише прогрес у stderr,
# а Windows PowerShell 5.1 у такому режимі перетворює це на термінальну помилку.
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$appFile = Join-Path $root "rtube\app.py"
# \r? — файл може бути і з LF, і з CRLF; $ у режимі (?m) стоїть лише перед \n.
$pattern = '(?m)^APP_VERSION = "([^"]+)"(?=\r?$)'

if (-not (Test-Path $python)) {
    Write-Host "Не знайдено .venv. Створіть його:" -ForegroundColor Yellow
    Write-Host "  py -3 -m venv .venv"
    Write-Host "  .venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller"
    exit 1
}

if ($UpdateYtdlp) {
    Write-Host "Оновлення yt-dlp…" -ForegroundColor Cyan
    & $python -m pip install --upgrade --quiet "yt-dlp[default]"
    $ver = & $python -c "import yt_dlp.version as v; print(v.__version__)"
    Write-Host "yt-dlp $ver — не забудьте оновити версію в requirements.txt" -ForegroundColor Cyan
}

# Читаємо й пишемо через .NET: командлети PowerShell 5.1 псують UTF-8 без BOM і LF.
$appText = [System.IO.File]::ReadAllText($appFile)
$match = [regex]::Match($appText, $pattern)
if (-not $match.Success) {
    Write-Host "Не знайдено APP_VERSION у $appFile" -ForegroundColor Red
    exit 1
}
$current = $match.Groups[1].Value
$target = $current
if ($Bump) {
    $parts = $current.Split('.')
    $parts[-1] = [string]([int]$parts[-1] + 1)
    $target = ($parts -join '.')
}

function Set-AppVersion([string]$value) {
    $text = [System.IO.File]::ReadAllText($appFile)
    $text = [regex]::Replace($text, $pattern, 'APP_VERSION = "' + $value + '"')
    [System.IO.File]::WriteAllText($appFile, $text, (New-Object System.Text.UTF8Encoding($false)))
}

if ($target -ne $current) {
    Set-AppVersion $target
    Write-Host "Версія: $current -> $target" -ForegroundColor Cyan
}

Write-Host "Тести…" -ForegroundColor Cyan
Push-Location $root
& $python -m unittest discover tests
$testsCode = $LASTEXITCODE
Pop-Location
if ($testsCode -ne 0) {
    if ($target -ne $current) { Set-AppVersion $current }
    Write-Host "Тести впали — збірку не запускаю." -ForegroundColor Red
    exit 1
}

Write-Host "Іконка…" -ForegroundColor Cyan
Push-Location $root
& $python make_icon.py | Out-Null
Pop-Location

Write-Host "Збірка R-TubeUA $target…" -ForegroundColor Cyan

# --collect-all yt_dlp_ejs: JS-скрипти розв'язувача лежать у пакеті як дані,
# і без цього ключа PyInstaller їх не бере — тоді у зібраному .exe зникли б
# усі дубльовані доріжки, хоча з .venv усе працювало б.
& $python -m PyInstaller `
    --onefile `
    --windowed `
    --clean `
    --noconfirm `
    --name R-TubeUA `
    --icon "$root\assets\logo.ico" `
    --add-data "$root\assets\logo.ico;assets" `
    --add-data "$root\assets\rozetka_theme.json;assets" `
    --collect-all customtkinter `
    --collect-all yt_dlp_ejs `
    --hidden-import truststore `
    --exclude-module pytest `
    --exclude-module numpy `
    --exclude-module pandas `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root\build" `
    "$root\main.py"

if ($LASTEXITCODE -ne 0) {
    if ($target -ne $current) {
        Set-AppVersion $current
        Write-Host "Версію повернуто на $current" -ForegroundColor Yellow
    }
    Write-Host "Збірка завершилася з помилкою." -ForegroundColor Red
    exit $LASTEXITCODE
}

$exe = Join-Path $root "dist\R-TubeUA.exe"
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Готово: $exe — версія $target ($size МБ)" -ForegroundColor Green
