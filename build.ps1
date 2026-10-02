# Збірка R-TubeUA в один .exe
#
# Запуск:
#   .\build.ps1                 зібрати з поточною версією
#   .\build.ps1 -Bump           підняти останнє число (1.0.0 -> 1.0.1) і зібрати
#   .\build.ps1 -UpdateYtdlp    спершу оновити yt-dlp до свіжої версії
#   .\build.ps1 -Bump -Release  зібрати й викласти реліз на GitHub
#   .\build.ps1 -Bump -Release -Notes "Що змінилось"   свій опис замість списку комітів
#
# yt-dlp вшивається в .exe, тож коли YouTube щось змінить і завантаження
# перестануть працювати, лікується це перезбіркою з -UpdateYtdlp — а щоб
# нова збірка дійшла до колег: .\build.ps1 -UpdateYtdlp -Bump -Release

param(
    [switch]$Bump,
    [switch]$UpdateYtdlp,
    [switch]$Release,
    [string]$Notes
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

# Реліз позначає ТЕГОМ конкретний коміт, тож дерево має бути чистим: інакше
# тег указував би на стан, якого в репозиторії немає. Перевіряємо до
# -UpdateYtdlp — той сам нічого в дереві не міняє, а requirements.txt
# правиться руками й має бути закомічений.
if ($Release) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Write-Host "Для -Release потрібен GitHub CLI: winget install GitHub.cli, потім gh auth login" -ForegroundColor Red
        exit 1
    }
    $dirty = git -C $root status --porcelain
    if ($dirty) {
        Write-Host "Робоче дерево брудне — закомітьте або сховайте зміни:" -ForegroundColor Red
        $dirty | ForEach-Object { Write-Host "  $_" }
        exit 1
    }
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

# Тег перевіряємо ДО збірки: інакше хвилина збірки йшла б у смітник через
# забутий -Bump.
if ($Release) {
    $tag = "v$target"
    if ((git -C $root tag -l $tag) -or (git -C $root ls-remote --tags origin $tag)) {
        Write-Host "Тег $tag уже існує. Додайте -Bump або приберіть тег." -ForegroundColor Red
        exit 1
    }
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

# Іконку НЕ перегенеровуємо: вона в репозиторії, а make_icon.py запускають
# руками, коли її справді міняють. Інакше кожна збірка могла б «забруднити»
# дерево новими байтами logo.ico, і коміт версії з -Release потягнув би їх.

Write-Host "Збірка R-TubeUA $target…" -ForegroundColor Cyan

# --collect-all yt_dlp_ejs: JS-скрипти розв'язувача лежать у пакеті як дані,
# і без цього ключа PyInstaller їх не бере — тоді у зібраному .exe зникли б
# усі дубльовані доріжки, хоча з .venv усе працювало б.
# --copy-metadata: з них програма знає версію вшитого yt-dlp і не підключає
# старіший з %APPDATA% після перезбірки зі свіжим (див. rtube/ytupdate.py).
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
    --copy-metadata yt-dlp `
    --copy-metadata yt-dlp-ejs `
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

if (-not $Release) { return }

# ─────────────────────────────────────────────────────────── реліз на GitHub
# Опис збираємо із заголовків комітів від попереднього тега — писати його
# руками щоразу ніхто не буде, а «що змінилося» питають завжди.
$previous = git -C $root describe --tags --abbrev=0 HEAD 2>$null
if ($Notes) {
    $body = $Notes
} elseif ($previous) {
    $subjects = git -C $root log "$previous..HEAD" --format="- %s"
    if ($subjects) {
        $body = "Зміни від $previous" + "`n`n" + ($subjects -join "`n")
    } else {
        $body = "Перезбірка без змін у коді (зокрема свіжий yt-dlp)."
    }
} else {
    $body = "Версія $target"
}
$ytdlp = & $python -c "import yt_dlp.version as v; print(v.__version__)"
$body += "`n`nyt-dlp $ytdlp"
$body += "`n`n---`n`nУстановлення не потрібне — один файл. ffmpeg програма за потреби " +
         "поставить сама (кнопка внизу вікна). Бажано мати Node.js ≥ 22 " +
         "(``winget install OpenJS.NodeJS.LTS``) — запас на випадок, якщо YouTube змінить правила видачі доріжок."

# Кирилицю передаємо ФАЙЛАМИ: Windows PowerShell 5.1 перекодовує аргументи
# зовнішніх програм у системну кодову сторінку, і git/gh отримали б «????».
$utf8 = New-Object System.Text.UTF8Encoding($false)
$msgFile = Join-Path $env:TEMP "rtube_commit_msg.txt"
$notesFile = Join-Path $env:TEMP "rtube_release_notes.md"
try {
    if ($target -ne $current) {
        [System.IO.File]::WriteAllText($msgFile, "Версія $target`n", $utf8)
        git -C $root add -- $appFile
        git -C $root commit -q -F $msgFile
        if ($LASTEXITCODE -ne 0) { Write-Host "Не вдалося закомітити версію." -ForegroundColor Red; exit 1 }
    }
    git -C $root push -q origin HEAD
    if ($LASTEXITCODE -ne 0) { Write-Host "Не вдалося запушити коміт." -ForegroundColor Red; exit 1 }

    [System.IO.File]::WriteAllText($notesFile, $body, $utf8)
    $head = (git -C $root rev-parse HEAD).Trim()
    Push-Location $root
    gh release create $tag $exe --target $head --title "R-TubeUA $target" --notes-file $notesFile --latest
    $ghCode = $LASTEXITCODE
    Pop-Location
    if ($ghCode -ne 0) { Write-Host "Не вдалося створити реліз." -ForegroundColor Red; exit 1 }
} finally {
    Remove-Item $msgFile, $notesFile -ErrorAction SilentlyContinue
}

git -C $root fetch -q --tags origin
Write-Host "Реліз $tag викладено" -ForegroundColor Green
