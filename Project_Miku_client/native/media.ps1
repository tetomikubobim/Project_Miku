# Windows media / volume control for Project Miku client.
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 reads BOM-less files as ANSI).
# Output: ONE line of base64(UTF-8 JSON) on stdout.
param([string]$Action, [int]$Value = -1)
$ErrorActionPreference = 'Stop'

function Out-Result($obj) {
  $json = $obj | ConvertTo-Json -Compress
  [Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
}

# ---------- media keys ----------
function Press-Key([byte]$vk) {
  if (-not ('MikuKey' -as [type])) {
    Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
public class MikuKey {
  [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, int dwFlags, int dwExtraInfo);
}
"@
  }
  [MikuKey]::keybd_event($vk, 0, 1, 0)
  Start-Sleep -Milliseconds 30
  [MikuKey]::keybd_event($vk, 0, 3, 0)
}

# ---------- Windows master volume (Core Audio) ----------
function Load-Audio {
  if ('MikuAudio' -as [type]) { return }
  Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f(); int g(); int h(); int i();
  int SetMasterVolumeLevelScalar(float fLevel, Guid pguidEventContext);
  int j();
  int GetMasterVolumeLevelScalar(out float pfLevel);
  int k(); int l(); int m(); int n();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
  int GetMute(out bool pbMute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid id, int clsCtx, int activationParams, out IAudioEndpointVolume aev); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int f(); int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }
public class MikuAudio {
  static IAudioEndpointVolume Vol() {
    var enumerator = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;
    IMMDevice dev = null;
    Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0, 1, out dev));
    IAudioEndpointVolume epv = null;
    Guid epvid = typeof(IAudioEndpointVolume).GUID;
    Marshal.ThrowExceptionForHR(dev.Activate(ref epvid, 23, 0, out epv));
    return epv;
  }
  public static float GetVolume() { float v = -1; Marshal.ThrowExceptionForHR(Vol().GetMasterVolumeLevelScalar(out v)); return v; }
  public static void SetVolume(float v) { Marshal.ThrowExceptionForHR(Vol().SetMasterVolumeLevelScalar(v, Guid.Empty)); }
  public static bool GetMute() { bool m = false; Marshal.ThrowExceptionForHR(Vol().GetMute(out m)); return m; }
  public static void SetMute(bool m) { Marshal.ThrowExceptionForHR(Vol().SetMute(m, Guid.Empty)); }
}
"@
}
# ---------- fallback: volume keys (used ONLY when the Core Audio COM call above fails) ----------
# VK_VOLUME_MUTE=0xAD, VK_VOLUME_DOWN=0xAE, VK_VOLUME_UP=0xAF. Windows moves 2% per key press.
function Press-Vol([byte]$vk, [int]$times) {
  for ($i = 0; $i -lt $times; $i++) { Press-Key $vk }
}
function Vol-Fallback([string]$act, [int]$val, [string]$why) {
  $r = @{ ok = $true; fallback = $true; comError = $why }
  switch ($act) {
    'volume-set' {
      $p = [Math]::Max(0, [Math]::Min(100, $val))
      Press-Vol 0xAE 50
      Press-Vol 0xAF ([int][Math]::Round($p / 2.0))
      $r.volume = $p
    }
    'volume-up' {
      $s = if ($val -ge 0) { $val } else { 10 }
      $n = [int][Math]::Ceiling($s / 2.0)
      Press-Vol 0xAF $n
      $r.dir = 'up'; $r.steps = $n * 2
    }
    'volume-down' {
      $s = if ($val -ge 0) { $val } else { 10 }
      $n = [int][Math]::Ceiling($s / 2.0)
      Press-Vol 0xAE $n
      $r.dir = 'down'; $r.steps = $n * 2
    }
    'mute'   { Press-Key 0xAD }
    'unmute' { Press-Vol 0xAF 1; Press-Vol 0xAE 1 }
    default  { return @{ ok = $false; error = $why } }
  }
  return $r
}
function Get-Pct { return [int][Math]::Round([MikuAudio]::GetVolume() * 100) }
function Set-Pct([int]$p) {
  $p = [Math]::Max(0, [Math]::Min(100, $p))
  [MikuAudio]::SetVolume([float]($p / 100.0))
  if ($p -gt 0 -and [MikuAudio]::GetMute()) { [MikuAudio]::SetMute($false) }
  return $p
}

# ---------- GlobalSystemMediaTransportControls (play/pause state, now playing) ----------
function Await($op, [Type]$type) {
  $task = $script:asTask.MakeGenericMethod($type).Invoke($null, @($op))
  [void]$task.Wait(-1)
  return $task.Result
}
function Get-MediaManager {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $script:asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]
  [void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
  [void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]
  $mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
  return (Await ($mgrType::RequestAsync()) $mgrType)
}
function Get-MediaSession { return (Get-MediaManager).GetCurrentSession() }
function Is-Playing($s) { return ($s.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing') }

try {
  switch ($Action) {
    'next' { Press-Key 0xB0; Out-Result @{ ok = $true } }
    'prev' { Press-Key 0xB1; Out-Result @{ ok = $true } }

    'play' {
      $did = 'toggled'
      $handled = $false
      try {
        $mgr = Get-MediaManager
        $sessions = @($mgr.GetSessions())
        if (@($sessions | Where-Object { Is-Playing $_ }).Count -gt 0) { $did = 'already-playing'; $handled = $true }
        else {
          $s = $mgr.GetCurrentSession()
          if ($s) {
            if (Await ($s.TryPlayAsync()) ([bool])) { $did = 'played'; $handled = $true }
          }
        }
      } catch { }
      if (-not $handled) { Press-Key 0xB3 }
      Out-Result @{ ok = $true; did = $did }
    }
    'pause' {
      # "current" session can be a paused one (e.g. another browser tab), so look at ALL sessions and pause every one that is playing
      $did = 'toggled'
      $handled = $false
      try {
        $sessions = @((Get-MediaManager).GetSessions())
        if ($sessions.Count -gt 0) {
          $playing = @($sessions | Where-Object { Is-Playing $_ })
          if ($playing.Count -eq 0) { $did = 'already-paused'; $handled = $true }
          else {
            $any = $false
            foreach ($s in $playing) { if (Await ($s.TryPauseAsync()) ([bool])) { $any = $true } }
            if ($any) { $did = 'paused'; $handled = $true }
          }
        }
      } catch { }
      if (-not $handled) { Press-Key 0xB3 }
      Out-Result @{ ok = $true; did = $did }
    }

    'nowplaying' {
      $s = Get-MediaSession
      if (-not $s) { Out-Result @{ ok = $true; found = $false } }
      else {
        $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
        $st = $s.GetPlaybackInfo().PlaybackStatus.ToString()
        Out-Result @{ ok = $true; found = $true; title = [string]$p.Title; artist = [string]$p.Artist; status = $st }
      }
    }

    'volume-set'  {
      try { Load-Audio; $v = Set-Pct $Value; Out-Result @{ ok = $true; volume = $v } }
      catch { Out-Result (Vol-Fallback $Action $Value $_.Exception.Message) }
    }
    'volume-up'   {
      try { Load-Audio; $b = Get-Pct; $step = if ($Value -ge 0) { $Value } else { 10 }; $a = Set-Pct ($b + $step); Out-Result @{ ok = $true; before = $b; volume = $a } }
      catch { Out-Result (Vol-Fallback $Action $Value $_.Exception.Message) }
    }
    'volume-down' {
      try { Load-Audio; $b = Get-Pct; $step = if ($Value -ge 0) { $Value } else { 10 }; $a = Set-Pct ($b - $step); Out-Result @{ ok = $true; before = $b; volume = $a } }
      catch { Out-Result (Vol-Fallback $Action $Value $_.Exception.Message) }
    }
    'volume-get'  { Load-Audio; Out-Result @{ ok = $true; volume = (Get-Pct); muted = [bool][MikuAudio]::GetMute() } }
    'mute'        {
      try { Load-Audio; [MikuAudio]::SetMute($true); Out-Result @{ ok = $true } }
      catch { Out-Result (Vol-Fallback $Action $Value $_.Exception.Message) }
    }
    'unmute'      {
      try { Load-Audio; [MikuAudio]::SetMute($false); Out-Result @{ ok = $true; volume = (Get-Pct) } }
      catch { Out-Result (Vol-Fallback $Action $Value $_.Exception.Message) }
    }

    default { Out-Result @{ ok = $false; error = "unknown action: $Action" } }
  }
} catch {
  Out-Result @{ ok = $false; error = $_.Exception.Message }
}
