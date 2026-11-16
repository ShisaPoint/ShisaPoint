# read_snapshot.ps1 - read ShisaPoint snapshot data, plain or secure-mode
# (Windows DPAPI, current user: only works when run as the same Windows account).
#   read_snapshot.ps1                 latest OCR text to stdout (default, same as -Text)
#   read_snapshot.ps1 -Meta           full meta JSON (incl. OCR words / window title)
#   read_snapshot.ps1 -Image [-Out p] decrypt latest image to a temp file, print its path
#   read_snapshot.ps1 -File <path> [-Out p]   decrypt any .dpapi file (text -> stdout, else -> -Out)
#   read_snapshot.ps1 -History        history.jsonl lines, decrypted, to stdout
#   read_snapshot.ps1 -Session [path] [-List|-Out dir]   saved session (.zip or .zip.dpapi;
#                                     default newest in sessions\): -List prints a frame-by-frame
#                                     summary (time, suggestions, boxes); otherwise extracts it to
#                                     -Out (default %TEMP%\shisa_session_<name>) and prints the folder
#   read_snapshot.ps1 -Session -All   list every saved session (session_* full saves and
#                                     follow_* Follow me saves): name, kind, date, size
#   read_snapshot.ps1 -Cleanup        delete temp files this script wrote (%TEMP%\shisa_* (also leftover peek_*))
param(
  [switch]$Text, [switch]$Meta, [switch]$Image, [switch]$History, [switch]$Cleanup,
  [string]$File, [string]$Out, [switch]$Session, [switch]$List, [switch]$All, [string]$Path,
  [string]$Dir = "$env:USERPROFILE\ShisaPoint\snapshots"
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$utf8 = New-Object Text.UTF8Encoding $false
$magic = [Text.Encoding]::ASCII.GetBytes("PPDPAPI1`n")
function Get-Bytes([string]$path) {
  $b = [IO.File]::ReadAllBytes($path)
  $enc = $b.Length -ge $magic.Length
  if ($enc) { for ($i = 0; $i -lt $magic.Length; $i++) { if ($b[$i] -ne $magic[$i]) { $enc = $false; break } } }
  if (-not $enc) { return ,$b }
  $blob = New-Object byte[] ($b.Length - $magic.Length)
  [Array]::Copy($b, $magic.Length, $blob, 0, $blob.Length)
  return ,([Security.Cryptography.ProtectedData]::Unprotect($blob, $null, 'CurrentUser'))
}
function Get-Text([string]$path) { $utf8.GetString((Get-Bytes $path)).TrimStart([char]0xFEFF) }
function First([string[]]$names) {
  foreach ($n in $names) { $p = Join-Path $Dir $n; if (Test-Path -LiteralPath $p) { return $p } }
  return $null
}
if ($Cleanup) {
  Get-ChildItem $env:TEMP -Filter 'shisa_*' -File -ErrorAction SilentlyContinue | Remove-Item -Force
  Get-ChildItem $env:TEMP -Filter 'peek_*' -File -ErrorAction SilentlyContinue | Remove-Item -Force
  Get-ChildItem $env:TEMP -Filter 'shisa_session_*' -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
  Get-ChildItem $env:TEMP -Filter 'peek_session_*' -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
  Write-Output 'CLEANED'; exit 0
}
if ($Session) {
  if ($All) {
    $sd = Join-Path $Dir 'sessions'
    Get-ChildItem $sd -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match '^(session|follow)_.*\.zip(\.dpapi)?$' } | Sort-Object LastWriteTime |
      ForEach-Object {
        $kind = if ($_.Name -like 'follow_*') { 'follow' } else { 'full' }
        $enc = if ($_.Name -like '*.dpapi') { ' encrypted' } else { '' }
        Write-Output ("{0}  {1}{2}  {3:yyyy-MM-dd HH:mm}  {4:N1} MB" -f $_.Name, $kind, $enc,
          $_.LastWriteTime, ($_.Length / 1MB))
      }
    exit 0
  }
  if (-not $Path -and $File) { $Path = $File }                   # -Session <path> binds positionally
  if (-not $Path -and $args.Count -gt 0) { $Path = [string]$args[0] }
  if (-not $Path) {
    $sd = Join-Path $Dir 'sessions'
    $f = Get-ChildItem $sd -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match '^(session|follow)_.*\.zip(\.dpapi)?$' } | Sort-Object LastWriteTime | Select-Object -Last 1
    if (-not $f) { Write-Output 'ERROR: no saved sessions'; exit 2 }
    $Path = $f.FullName
  }
  Add-Type -AssemblyName System.IO.Compression
  $ms = New-Object IO.MemoryStream (,(Get-Bytes $Path))          # decrypted in memory only
  $zip = New-Object IO.Compression.ZipArchive ($ms, [IO.Compression.ZipArchiveMode]::Read)
  try {
    if ($List) {
      $e = $zip.GetEntry('manifest.json')
      if (-not $e) { Write-Output 'ERROR: no manifest.json'; exit 2 }
      $r = New-Object IO.StreamReader ($e.Open(), $utf8)
      $man = $r.ReadToEnd() | ConvertFrom-Json; $r.Close()
      $kind = if ($man.kind) { $man.kind } else { 'full' }
      Write-Output ("SESSION {0}  kind={1}  frames={2}  marks={3}  created={4}" -f (Split-Path $Path -Leaf),
        $kind, $man.frame_count, [bool]$man.has_marks, $man.created)
      $k = 0
      foreach ($r in @($man.follow_ranges)) {
        if (-not $r) { continue }
        $k++
        Write-Output ("FOLLOW {0}: {1} -> {2}  ended: {3}  frames {4}-{5}" -f $k, $r.start, $r.stop,
          $r.stop_reason, ($r.first_frame + 1), ($r.last_frame + 1))
      }
      $i = 0
      foreach ($fr in $man.frames) {
        $i++
        $ov = $fr.overlay
        $fm = if ($fr.follow) { 'F' } else { ' ' }
        $line = "[{0}]{1} {2}  {3}  {4}" -f $i, $fm, $fr.timestamp, $fr.file, $fr.window_title
        Write-Output $line
        if ($ov) {
          foreach ($sg in $ov.suggestions) {
            $flag = @(); if ($sg.gone) { $flag += 'gone' }; if ($sg.old) { $flag += 'old' }
            $box = if ($sg.box) { 'box=' + ($sg.box -join ',') } elseif ($sg.circle) { 'circle=' + ($sg.circle -join ',') } else { 'no box' }
            Write-Output ("    {0}. {1}  -> {2}  {3} {4}" -f $sg.n, $sg.text, $sg.target, $box, ($flag -join ' '))
          }
          if ($ov.marks) { Write-Output ("    marks: {0}" -f (@($ov.marks | ForEach-Object { $_.type }) -join ', ')) }
          if ($ov.visible -eq $false) { Write-Output '    (markup hidden)' }
        }
      }
      exit 0
    }
    $name = (Split-Path $Path -Leaf) -replace '\.dpapi$', '' -replace '\.zip$', ''
    if (-not $Out) { $Out = Join-Path $env:TEMP ('shisa_session_' + $name) }
    foreach ($e in $zip.Entries) {
      if (-not $e.Name) { continue }
      $dest = [IO.Path]::GetFullPath((Join-Path $Out $e.FullName))
      if (-not $dest.StartsWith([IO.Path]::GetFullPath($Out))) { continue }   # no path escapes
      [IO.Directory]::CreateDirectory((Split-Path $dest)) | Out-Null
      $in = $e.Open(); $fs = [IO.File]::Create($dest); $in.CopyTo($fs); $fs.Close(); $in.Close()
    }
    Write-Output $Out
    exit 0
  } finally { $zip.Dispose(); $ms.Dispose() }
}
if ($File) {
  $name = Split-Path $File -Leaf
  $plain = $name -replace '\.dpapi$', ''
  $isText = $plain -match '\.(txt|json|jsonl|csv|log)$'
  if ($isText -and -not $Out) { Write-Output (Get-Text $File); exit 0 }
  if (-not $Out) { $Out = Join-Path $env:TEMP ('shisa_' + $plain) }
  [IO.File]::WriteAllBytes($Out, (Get-Bytes $File)); Write-Output $Out; exit 0
}
if ($Image) {
  $src = First @('latest.jpg.dpapi', 'latest.png.dpapi', 'latest.jpg', 'latest.png')
  if (-not $src) { Write-Output 'ERROR: no latest image'; exit 2 }
  $plain = (Split-Path $src -Leaf) -replace '\.dpapi$', ''
  if (-not $Out) { $Out = Join-Path $env:TEMP ('shisa_' + $plain) }
  [IO.File]::WriteAllBytes($Out, (Get-Bytes $src)); Write-Output $Out; exit 0
}
if ($Meta) {
  $p = Join-Path $Dir 'meta.json'
  $m = Get-Text $p
  $sp = Join-Path $Dir 'meta.secure.dpapi'
  if (($m | ConvertFrom-Json).secure -eq $true -and (Test-Path $sp)) { $m = Get-Text $sp }
  Write-Output $m; exit 0
}
if ($History) {
  $p = Join-Path $Dir 'history.jsonl'
  if (-not (Test-Path $p)) { exit 0 }
  foreach ($l in [IO.File]::ReadAllLines($p)) {
    if ($l.StartsWith('PPDP1:')) {
      $b = [Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($l.Substring(6)), $null, 'CurrentUser')
      Write-Output $utf8.GetString($b)
    } elseif ($l.Trim()) { Write-Output $l }
  }
  exit 0
}
$src = First @('latest.txt.dpapi', 'latest.txt')
if (-not $src) { Write-Output 'ERROR: no OCR text yet'; exit 2 }
Write-Output (Get-Text $src)
