# Jarvis updater (run through update.bat). Copies this folder's program files over an
# existing Jarvis install. Never touches %APPDATA%\Jarvis (settings, notes, keys) or .venv.
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot

function Say($t, $c = 'Gray') { Write-Host " $t" -ForegroundColor $c }
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
$dest = $null
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Jarvis.lnk'
if (Test-Path -LiteralPath $lnk) {
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
$ans = Read-Host ' Update now? (Y/N)'
if ($ans -notmatch '^[Yy]') { Say 'Nothing changed.'; exit 1 }

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
for ($try = 1; $try -le 3; $try++) {
    $procs = @(Get-JarvisProcs)
    if (-not $procs.Count) { break }
    Say 'Closing Jarvis...'
    $procs | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
    Start-Sleep -Seconds 2
}
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
    exit 1
}

# 4. Start the new version.
Say "Updated to version $new." Green
Start-Process -FilePath (Join-Path $dest '.venv\Scripts\pythonw.exe') -ArgumentList "`"$pyw`"" -WorkingDirectory $dest
Say 'Jarvis is starting (tray icon, bottom-right). You can delete this update folder now.'
exit 0
