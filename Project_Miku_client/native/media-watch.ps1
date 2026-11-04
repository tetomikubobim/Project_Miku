# Windows media (SMTC) watcher for Project Miku: long-running, prints one base64(UTF-8 JSON) line per change.
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 reads BOM-less files as ANSI).
# Line: { found, app, title, artist, album, status, pos, duration, rate, ts }   pos/duration are seconds
param([int]$IntervalMs = 500, [string]$Apps = '', [int]$ParentPid = 0)
$ErrorActionPreference = 'Stop'

function Out-Line($obj) {
  $json = $obj | ConvertTo-Json -Compress
  [Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
  [Console]::Out.Flush()
}

function Await($op, [Type]$type) {
  $task = $script:asTask.MakeGenericMethod($type).Invoke($null, @($op))
  [void]$task.Wait(-1)
  return $task.Result
}

try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $script:asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]
  [void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
  [void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]
  $mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
  $propType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]
  $mgr = Await ($mgrType::RequestAsync()) $mgrType
} catch {
  Out-Line @{ error = ('init: ' + $_.Exception.Message) }
  exit 1
}

# A session without a title (e.g. an app that only registers a media session) is ignored so it cannot hide the real song
function Has-Title($s) {
  try { $p = Await ($s.TryGetMediaPropertiesAsync()) $propType; return (-not [string]::IsNullOrWhiteSpace([string]$p.Title)) } catch { return $false }
}

$lastKey = $null
$lastEmitAt = [DateTime]::MinValue
$lastPos = 0.0
$lastErr = ''

while ($true) {
  if ($ParentPid -gt 0 -and -not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }
  try {
    $sessions = @($mgr.GetSessions())
    if ($Apps) { $sessions = @($sessions | Where-Object { $_.SourceAppUserModelId -match $Apps }) }
    $sessions = @($sessions | Where-Object { Has-Title $_ })

    $pick = $null
    if ($sessions.Count -gt 0) {
      $playing = @($sessions | Where-Object { $_.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing' })
      if ($playing.Count -gt 0) {
        $cur = $mgr.GetCurrentSession()
        $pick = $playing[0]
        if ($cur) { foreach ($p in $playing) { if ($p.SourceAppUserModelId -eq $cur.SourceAppUserModelId) { $pick = $p; break } } }
      } else {
        $cur = $mgr.GetCurrentSession()
        $pick = $sessions[0]
        if ($cur) { foreach ($p in $sessions) { if ($p.SourceAppUserModelId -eq $cur.SourceAppUserModelId) { $pick = $p; break } } }
      }
    }

    if (-not $pick) {
      $key = 'none'
      if ($key -ne $lastKey) { Out-Line @{ found = $false; ts = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }; $lastKey = $key; $lastEmitAt = [DateTime]::UtcNow }
    } else {
      $props = Await ($pick.TryGetMediaPropertiesAsync()) $propType
      $info = $pick.GetPlaybackInfo()
      $status = $info.PlaybackStatus.ToString()
      $rate = 1.0
      if ($null -ne $info.PlaybackRate) { $rate = [double]$info.PlaybackRate }
      if ($rate -le 0) { $rate = 1.0 }
      $tl = $pick.GetTimelineProperties()
      $pos = [double]$tl.Position.TotalSeconds
      $dur = [double]($tl.EndTime.TotalSeconds - $tl.StartTime.TotalSeconds)
      if ($dur -lt 0) { $dur = 0 }
      $upd = $tl.LastUpdatedTime
      $rawPos = [Math]::Round([double]$tl.Position.TotalSeconds, 2)
      $ageSec = -1
      if ($upd.Year -ge 2000) { $ageSec = [Math]::Round(([DateTimeOffset]::UtcNow - $upd).TotalSeconds, 1) }
      if ($status -eq 'Playing' -and $upd.Year -ge 2000) {
        $age = ([DateTimeOffset]::UtcNow - $upd).TotalSeconds
        if ($age -gt 0 -and $age -lt 21600) { $pos = $pos + $age * $rate }
      }
      if ($dur -gt 0 -and $pos -gt $dur) { $pos = $dur }

      $app = [string]$pick.SourceAppUserModelId
      $key = $app + '|' + [string]$props.Title + '|' + [string]$props.Artist + '|' + $status + '|' + [Math]::Round($dur)
      $now = [DateTime]::UtcNow
      $since = ($now - $lastEmitAt).TotalSeconds
      $expect = $lastPos
      if ($lastKey -and $lastKey.Contains('|Playing|')) { $expect = $lastPos + $since * $rate }
      $jump = [Math]::Abs($pos - $expect) -gt 1.5
      if ($key -ne $lastKey -or $jump -or $since -ge 1.5) {
        Out-Line @{
          found = $true; app = $app; title = [string]$props.Title; artist = [string]$props.Artist; album = [string]$props.AlbumTitle
          status = $status; pos = [Math]::Round($pos, 2); duration = [Math]::Round($dur, 2); rate = $rate; rawPos = $rawPos; age = $ageSec
          ts = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        }
        $lastKey = $key; $lastEmitAt = $now; $lastPos = $pos
      }
    }
    $lastErr = ''
  } catch {
    $msg = $_.Exception.Message
    if ($msg -ne $lastErr) { Out-Line @{ error = $msg }; $lastErr = $msg }
  }
  Start-Sleep -Milliseconds $IntervalMs
}
