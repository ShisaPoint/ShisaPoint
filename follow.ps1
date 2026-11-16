# follow.ps1 - wait for the next *finished* ShisaPoint snapshot.
# Polls meta.json every 700 ms. Reports only snapshots that are confirmed (passed the
# second look / ended in punctuation / interval) or forced 'checkin' snapshots, and only
# when the OCR text differs from .follow_last.txt. The first read of a new session
# (new follow_expires) only records a baseline. Time/date-only lines are ignored.
# -Now prints the current state immediately (latest.txt + image path + SECURE status)
# in the same report format, without baselining or waiting, then exits. Exits:
#   new text      -> prints TIME/TRIGGER/LATEST/SCALE + the new lines
#   session over  -> prints SESSION ENDED   (follow_active false)
#   timeout       -> prints NO CHANGE
# Secure mode (meta.json "secure": true): OCR text comes from meta.secure.dpapi (Windows DPAPI,
# current user) and .follow_last.txt is stored encrypted too. Plain and encrypted files both
# work, so turning secure mode on/off needs no changes here.
param(
  [int]$TimeoutSec = 60,
  [string]$Dir = "$env:USERPROFILE\ShisaPoint\snapshots",
  [switch]$Now
)
$meta = Join-Path $Dir 'meta.json'
$lastFile = Join-Path $Dir '.follow_last.txt'
$sessFile = Join-Path $Dir '.follow_session.txt'   # follow_expires of the baselined session
# lines that are only a time or date (taskbar clock etc.) never count as new text
$clock = '^\s*(\d{1,2}[:.]\d{2}([:.]\d{2})?(\s*[AaPp]\.?\s*[Mm]\.?)?(\s+\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4})?|\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4})\s*$'
# editor status-bar noise (Notepad / WordPad / VS Code) never counts as new text
$noise = '^\s*(Ln\s*\d+\s*,\s*Col\s*\d+|\d[\d,]*\s+characters?|\d{1,3}\s*%|Windows\s*\(CRLF\)|Unix\s*\(LF\)|Macintosh\s*\(CR\)|UTF-?8(\s+with\s+BOM)?|UTF-16\s*(LE|BE)|ANSI|Plain\s+text)(\s*[|·]?\s*(Ln\s*\d+\s*,\s*Col\s*\d+|\d[\d,]*\s+characters?|\d{1,3}\s*%|Windows\s*\(CRLF\)|UTF-?8|Plain\s+text))*\s*$'
$utf8 = New-Object Text.UTF8Encoding $false
Add-Type -AssemblyName System.Security
$magic = [Text.Encoding]::ASCII.GetBytes("PPDPAPI1`n")
function Read-Prot([string]$path) {
  # file text; DPAPI files (magic header) are decrypted for the current user
  $b = [IO.File]::ReadAllBytes($path)
  $enc = $b.Length -ge $magic.Length
  if ($enc) { for ($i = 0; $i -lt $magic.Length; $i++) { if ($b[$i] -ne $magic[$i]) { $enc = $false; break } } }
  if ($enc) {
    $blob = New-Object byte[] ($b.Length - $magic.Length)
    [Array]::Copy($b, $magic.Length, $blob, 0, $blob.Length)
    $b = [Security.Cryptography.ProtectedData]::Unprotect($blob, $null, 'CurrentUser')
  }
  return $utf8.GetString($b).TrimStart([char]0xFEFF)
}
function Write-Last([string]$text, [bool]$secure) {
  if ($secure) {
    $p = [Security.Cryptography.ProtectedData]::Protect($utf8.GetBytes($text), $null, 'CurrentUser')
    [IO.File]::WriteAllBytes($lastFile, [byte[]]($magic + $p))
  } else { [IO.File]::WriteAllText($lastFile, $text, $utf8) }
}
function Read-CurrentMeta {
  try { $m = [IO.File]::ReadAllText($meta) | ConvertFrom-Json } catch { $m = $null }
  $secure = $false
  if ($m -and $m.secure -eq $true) {
    $secure = $true
    $sp = Join-Path $Dir 'meta.secure.dpapi'
    if (Test-Path $sp) {
      try {
        $full = Read-Prot $sp | ConvertFrom-Json
        foreach ($k in 'follow_active','follow_end_reason','follow_expires','follow_remaining_s') {
          if ($m.PSObject.Properties.Name -contains $k) {
            $full | Add-Member -NotePropertyName $k -NotePropertyValue $m.$k -Force
          }
        }
        $m = $full
      } catch { }
    }
  }
  return @{ Meta = $m; Secure = $secure }
}

if ($Now) {
  $cur = Read-CurrentMeta
  $m = $cur.Meta
  $secure = $cur.Secure
  if (-not $m) { Write-Output 'NO META'; exit 1 }
  $text = $null
  $tp = Join-Path $Dir $(if ($secure -and (Test-Path (Join-Path $Dir 'latest.txt.dpapi'))) { 'latest.txt.dpapi' } else { 'latest.txt' })
  if (Test-Path $tp) { try { $text = Read-Prot $tp } catch { $text = $null } }
  if ($null -eq $text -and $m.ocr) { $text = [string]$m.ocr.text }
  Write-Output "TIME: $($m.timestamp)"
  Write-Output "TRIGGER: $($m.trigger)  CONFIRM: $($m.confirm_reason)"
  Write-Output "LATEST: $(Join-Path $Dir $m.latest)"
  Write-Output "SCALE: $($m.scale)"
  if ($secure) { Write-Output "SECURE: on (image is encrypted: read_snapshot.ps1 -Image gives a viewable copy)" }
  if ($m.follow_active) { Write-Output "FOLLOW LEFT: $($m.follow_remaining_s)s" }
  Write-Output '--- NEW LINES ---'
  if ($null -ne $text) { $text -split "`n" | ForEach-Object { Write-Output $_ } }
  exit 0
}

$last = ''
if (Test-Path $lastFile) { try { $last = Read-Prot $lastFile } catch { $last = '' } }
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
  try { $m = [IO.File]::ReadAllText($meta) | ConvertFrom-Json } catch { $m = $null }
  $secure = $false
  if ($m -and $m.secure -eq $true) {
    $secure = $true
    $sp = Join-Path $Dir 'meta.secure.dpapi'
    if (Test-Path $sp) {
      try {
        $full = Read-Prot $sp | ConvertFrom-Json
        # plain meta.json is written last: its follow state wins
        foreach ($k in 'follow_active','follow_end_reason','follow_expires','follow_remaining_s') {
          if ($m.PSObject.Properties.Name -contains $k) {
            $full | Add-Member -NotePropertyName $k -NotePropertyValue $m.$k -Force
          }
        }
        $m = $full
      } catch { }
    }
  }
  if ($m) {
    if ($m.PSObject.Properties.Name -contains 'follow_active' -and $m.follow_active -ne $true) {
      $why = if ($m.follow_end_reason) { " ($($m.follow_end_reason))" } else { '' }
      Write-Output "SESSION ENDED$why"
      exit 0
    }
    $text0 = if ($m.ocr) { [string]$m.ocr.text } else { $null }
    $sess = [string]$m.follow_expires
    $known = if (Test-Path $sessFile) { [IO.File]::ReadAllText($sessFile) } else { '' }
    # a new session (new follow_expires) or its session_start snapshot is the baseline
    if ($null -ne $text0 -and (($sess -and $sess -ne $known) -or ($m.trigger -eq 'session_start' -and $text0 -ne $last))) {
      # first look at a new session: record a baseline instead of dumping everything
      Write-Last $text0 $secure
      [IO.File]::WriteAllText($sessFile, $sess, $utf8)
      $last = $text0
      Write-Output "BASELINE recorded (session until $sess)"
    }
    $ok = ($m.confirmed -eq $true) -or ($m.trigger -eq 'checkin')
    $text = if ($m.ocr) { [string]$m.ocr.text } else { $null }
    if ($ok -and $null -ne $text -and $text -ne $last) {
      $old = $last -split "`n"
      $new = @($text -split "`n" | Where-Object { $old -notcontains $_ -and $_.Trim() -and $_ -notmatch $clock -and $_ -notmatch $noise })
      Write-Last $text $secure
      $last = $text
      if ($new.Count -eq 0) { Start-Sleep -Milliseconds 700; continue }   # only clock/date changed
      Write-Output "TIME: $($m.timestamp)"
      Write-Output "TRIGGER: $($m.trigger)  CONFIRM: $($m.confirm_reason)"
      Write-Output "LATEST: $(Join-Path $Dir $m.latest)"
      Write-Output "SCALE: $($m.scale)"
      if ($secure) { Write-Output "SECURE: on (image is encrypted: read_snapshot.ps1 -Image gives a viewable copy)" }
      if ($m.follow_active) { Write-Output "FOLLOW LEFT: $($m.follow_remaining_s)s" }
      Write-Output '--- NEW LINES ---'
      $new | ForEach-Object { Write-Output $_ }
      exit 0
    }
  }
  Start-Sleep -Milliseconds 700
}
Write-Output 'NO CHANGE'
