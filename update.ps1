# Jarvis updater. Copies new program files over an existing Jarvis install.
# Never touches %APPDATA%\Jarvis (settings, notes, keys) or .venv.
#  - update.bat runs it from an unzipped new version (asks before changing anything).
#  - Jarvis's "Update" button runs it with -Source <verified download> -Dest <install> -Auto -WaitPid <Jarvis>
#    after the user clicked Update, so it doesn't ask again.
param([string]$Source, [string]$Dest, [switch]$Auto, [int]$WaitPid = 0)
$ErrorActionPreference = 'Stop'
$src = if ($Source) { (Resolve-Path -LiteralPath $Source).Path } else { $PSScriptRoot }
if ($Auto) {
    $Host.UI.RawUI.WindowTitle = 'Updating Jarvis'
    $logDir = Join-Path $env:APPDATA 'Jarvis\logs'
    try { New-Item -ItemType Directory -Force $logDir | Out-Null
          Start-Transcript -LiteralPath (Join-Path $logDir 'update.log') -Force | Out-Null } catch {}
}

function Say($t, $c = 'Gray') { Write-Host " $t" -ForegroundColor $c }
function Stop-Auto($msg) {      # -Auto has nobody to answer questions: explain, wait, give up
    Say $msg Red
    Say 'Nothing was changed. This window closes in 30 seconds.'
    Start-Sleep -Seconds 30
    exit 1
}
function Test-Install($p) {
    $p -and (Test-Path -LiteralPath (Join-Path $p 'Jarvis.pyw')) -and
        (Test-Path -LiteralPath (Join-Path $p '.venv\Scripts\python.exe'))
}
function Get-Version($p) {
    $f = Join-Path $p 'jarvis\__init__.py'
    if (Test-Path -LiteralPath $f) {
        $m = Select-String -LiteralPath $f -Pattern '__version__\s*=\s*"([^"]+)"'
        if ($m) { return $m.Matches[0].Groups[1].Value }
    }
    '?'
}
function Copy-Tree($from, $to, [switch]$Mirror) {
    $how = if ($Mirror) { '/MIR' } else { '/E' }
    robocopy $from $to $how /XD .venv _previous __pycache__ .git /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying files failed (robocopy code $LASTEXITCODE)." }
}

# 1. Find the installed copy: the Start Menu shortcut points at it.
$dest = $Dest
if ($Auto -and -not (Test-Install $dest)) { Stop-Auto "Update failed: $dest isn't a set-up Jarvis folder." }
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Jarvis.lnk'
if (-not $dest -and (Test-Path -LiteralPath $lnk)) {
    try { $dest = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk).WorkingDirectory } catch {}
}
while (-not (Test-Install $dest)) {
    Say "Couldn't find your Jarvis folder automatically." Yellow
    Say "Drag your Jarvis folder (the one with run.bat in it) into this window, then press Enter."
    $dest = (Read-Host ' Folder').Trim().Trim('"')
    if (-not $dest) { Say 'Nothing changed.'; exit 1 }
    if (-not (Test-Install $dest)) { Say "That folder isn't a set-up Jarvis (no Jarvis.pyw or .venv in it)." Red }
}
$dest = (Resolve-Path -LiteralPath $dest).Path.TrimEnd('\')
if ($dest -eq $src.TrimEnd('\')) {
    Say "This IS your Jarvis folder, so there's nothing to update from." Yellow
    Say 'Unzip the new Jarvis zip somewhere else (e.g. Downloads) and run update.bat from there.'
    exit 1
}

$old, $new = (Get-Version $dest), (Get-Version $src)
Say "Your Jarvis:  $dest  (version $old)"
Say "Update to:    version $new" Cyan
Say 'Your settings, notes and keys are kept.'
if (-not $Auto) {
    $ans = Read-Host ' Update now? (Y/N)'
    if ($ans -notmatch '^[Yy]') { Say 'Nothing changed.'; exit 1 }
} else {
    Say 'Please leave this window open. It closes by itself when the update is done.' Cyan
}

# 2. Close Jarvis if it's running from that folder.
#    run.bat starts ".venv\Scripts\pythonw.exe Jarvis.pyw" with relative paths, and the venv
#    launcher then starts the real Python as a child, so match the launcher by its exe path and
#    take its children too. If an old copy kept running, the restart below would only bring
#    the OLD Jarvis to the front (single-instance check) and the update would seem not to work.
$pyw = Join-Path $dest 'Jarvis.pyw'
$venv = Join-Path $dest '.venv\'
$pywL, $venvL = $pyw.ToLower(), $venv.ToLower()
function Get-JarvisProcs {
    $all = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'")
    $mine = @($all | Where-Object {
        $exe = "$($_.ExecutablePath)".ToLower(); $cmd = "$($_.CommandLine)".ToLower()
        $exe.StartsWith($venvL) -or $cmd.Contains($pywL) -or $cmd.Contains($venvL) })
    $ids = @($mine | ForEach-Object { $_.ProcessId })
    @($mine) + @($all | Where-Object { $ids -contains $_.ParentProcessId -and $ids -notcontains $_.ProcessId })
}
if ($WaitPid) {     # Jarvis closes itself after starting us; give it a moment
    try { Wait-Process -Id $WaitPid -Timeout 15 -ErrorAction Stop } catch {}
}
for ($try = 1; $try -le 3; $try++) {
    $procs = @(Get-JarvisProcs)
    if (-not $procs.Count) { break }
    Say 'Closing Jarvis...'
    $procs | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
    Start-Sleep -Seconds 2
}
if ($Auto -and @(Get-JarvisProcs).Count) { Stop-Auto "Jarvis didn't close, so I couldn't update it." }
while (@(Get-JarvisProcs).Count) {
    Say "Jarvis is still running. Right-click its tray icon (bottom-right) and click Quit, then press Enter." Yellow
    Read-Host ' ' | Out-Null
}

# 3. Back up the current program files (for rollback), then copy the new ones in.
$prev = Join-Path $dest '_previous'
$reqOld = Join-Path $dest 'requirements.txt'
$reqHashOld = if (Test-Path -LiteralPath $reqOld) { (Get-FileHash -LiteralPath $reqOld).Hash } else { '' }
try {
    Say 'Backing up the old version...'
    if (Test-Path -LiteralPath $prev) { Remove-Item -LiteralPath $prev -Recurse -Force }
    Copy-Tree $dest $prev
    Say 'Copying the new version...'
    Copy-Tree (Join-Path $src 'jarvis') (Join-Path $dest 'jarvis') -Mirror   # drops removed modules
    Copy-Tree $src $dest
    if ((Get-FileHash -LiteralPath $reqOld).Hash -ne $reqHashOld) {
        Say 'Installing new parts Jarvis needs (can take a few minutes)...'
        & (Join-Path $dest '.venv\Scripts\python.exe') -m pip install -r $reqOld --disable-pip-version-check
        if ($LASTEXITCODE) { throw 'Installing the new parts failed. Check your internet connection.' }
    }
} catch {
    Say "Update failed: $($_.Exception.Message)" Red
    if (Test-Path -LiteralPath (Join-Path $prev 'jarvis')) {
        Say 'Putting the old version back...' Yellow
        Copy-Tree (Join-Path $prev 'jarvis') (Join-Path $dest 'jarvis') -Mirror
        Copy-Tree $prev $dest
        Say 'Old version restored.'
    }
    if ($Auto) { Start-Process -FilePath (Join-Path $dest '.venv\Scripts\pythonw.exe') -ArgumentList "`"$pyw`"" -WorkingDirectory $dest; Start-Sleep -Seconds 30 }
    exit 1
}

# 4. Start the new version. If it quits straight away, put the old version back.
function Restore-Previous {
    Copy-Tree (Join-Path $prev 'jarvis') (Join-Path $dest 'jarvis') -Mirror
    Copy-Tree $prev $dest
}
$pythonw = Join-Path $dest '.venv\Scripts\pythonw.exe'
Say "Updated to version $new. Starting it..." Green
$p = Start-Process -FilePath $pythonw -ArgumentList "`"$pyw`"" -WorkingDirectory $dest -PassThru
Say 'Checking that it starts properly (20 seconds)...'
if ($p.WaitForExit(20000)) {
    Say "Version $new closed right after starting, so I'm putting version $old back." Yellow
    Restore-Previous
    Start-Process -FilePath $pythonw -ArgumentList "`"$pyw`"" -WorkingDirectory $dest
    Say "Version $old is back. The error is in the log file %APPDATA%\Jarvis\logs\jarvis.log." Yellow
    if ($Auto) { Start-Sleep -Seconds 30 } 
    exit 1
}
Say 'Jarvis is running (tray icon, bottom-right).' Green
if ($Auto) { Say 'This window closes in 5 seconds.'; Start-Sleep -Seconds 5 }
else { Say 'You can delete this update folder now.' }
exit 0
