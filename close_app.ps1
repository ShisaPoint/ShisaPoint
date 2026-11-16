# close_app.ps1 - close ShisaPoint cleanly, e.g. before a deploy.
# 1) Signals the app's named event Local\ShisaPoint_Quit -> the app quits normally and the
#    PyInstaller bootloader deletes its %TEMP%\_MEI* folder.
#    Also tries Local\PeekAndPoint_Quit and DesktopLens.exe so a rename deploy can still
#    stop the previous build.
# 2) Fallback after -TimeoutSec: kill only the app (child) process so the bootloader (parent)
#    still cleans _MEI; then kill anything left and delete orphan ShisaPoint/DesktopLens _MEI folders.
# Output: NOT RUNNING | CLOSED (event|fallback-child|fallback-kill) ... MEI_LEFT=<n>
param([int]$TimeoutSec = 15)
$ErrorActionPreference = 'Continue'
$names = @('ShisaPoint.exe', 'DesktopLens.exe')
$events = @('Local\ShisaPoint_Quit', 'Local\PeekAndPoint_Quit')
function Procs {
  $all = @()
  foreach ($n in $names) { $all += @(Get-CimInstance Win32_Process -Filter "Name='$n'" -EA 0) }
  $all
}
function OurMei { @(Get-ChildItem $env:TEMP -Directory -Filter '_MEI*' -Force -EA 0 |
                    Where-Object {
                      (Test-Path (Join-Path $_.FullName 'icon\ShisaPoint.ico')) -or
                      (Test-Path (Join-Path $_.FullName 'icon\DesktopLens.ico'))
                    }) }
function WaitGone([int]$sec) { $t = (Get-Date).AddSeconds($sec)
  while ((Procs).Count -and (Get-Date) -lt $t) { Start-Sleep -Milliseconds 300 }; -not (Procs).Count }
if (-not (Procs).Count) { "NOT RUNNING MEI_LEFT=$((OurMei).Count)"; exit 0 }
$how = 'event'
$signaled = $false
foreach ($en in $events) {
  try {
    $ev = [Threading.EventWaitHandle]::OpenExisting($en)
    [void]$ev.Set(); $ev.Dispose()
    $signaled = $true
  } catch { }
}
if (-not $signaled) { $how = 'no-event' }
$ok = $signaled -and (WaitGone $TimeoutSec)
if (-not $ok) {
  $ps = Procs; $ids = $ps.ProcessId
  $child = $ps | Where-Object { $ids -contains $_.ParentProcessId }
  if (-not $child) { $child = $ps }
  $how = 'fallback-child'
  $child | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA 0 }
  if (-not (WaitGone 8)) { $how = 'fallback-kill'; Procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA 0 }; [void](WaitGone 5) }
}
Start-Sleep -Milliseconds 500
$left = OurMei
if ($left.Count -and -not (Procs).Count) {
  $left | ForEach-Object { Remove-Item $_.FullName -Recurse -Force -EA 0 }
  $removed = $left.Count; $left = OurMei
  "CLOSED ($how) removed $removed orphan _MEI MEI_LEFT=$($left.Count)"
} else { "CLOSED ($how) MEI_LEFT=$($left.Count)" }
