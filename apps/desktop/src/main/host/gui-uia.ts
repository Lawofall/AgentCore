/**
 * Windows UI Automation helpers for Computer Use.
 * Scripts return a single JSON object on stdout.
 */

import { runPowerShellEncoded } from "./powershell";

const UIA_PREAMBLE = `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class HostGuiNative {
  [StructLayout(LayoutKind.Sequential)]
  public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint nFlags);
  public static uint LastInputTick() {
    LASTINPUTINFO info = new LASTINPUTINFO();
    info.cbSize = (uint)Marshal.SizeOf(info);
    GetLastInputInfo(ref info);
    return info.dwTime;
  }
  public static uint FgPid() {
    uint pid;
    GetWindowThreadProcessId(GetForegroundWindow(), out pid);
    return pid;
  }
}
"@
function Emit($obj) {
  $obj | ConvertTo-Json -Compress -Depth 8
}
function Fail($kind, $detail) {
  Emit @{ ok = $false; kind = $kind; detail = $detail }
}
$guiIn = $InputJson | ConvertFrom-Json
`;

const ATTACH_SCRIPT = `
${UIA_PREAMBLE}
try {
  Add-Type -AssemblyName System.Windows.Forms
  $root = [System.Windows.Automation.AutomationElement]::RootElement
  $cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Window)
  $windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
  $excludePid = [int]$guiIn.electron_pid
  $rows = New-Object System.Collections.Generic.List[object]
  foreach ($w in $windows) {
    $name = [string]$w.Current.Name
    if ([string]::IsNullOrWhiteSpace($name)) { continue }
    $pid = [int]$w.Current.ProcessId
    if ($pid -eq $excludePid) { continue }
    $hwnd = [int64]$w.Current.NativeWindowHandle
    if ($hwnd -eq 0) { continue }
    if (-not $w.Current.IsOffscreen) {
      $rows.Add([pscustomobject]@{ Title = $name; Pid = $pid; Hwnd = $hwnd })
    }
  }
  if ($rows.Count -eq 0) {
    Fail 'HostGuiEmptyTree' '没有可附着的窗口。'
    return
  }
  $form = New-Object System.Windows.Forms.Form
  $form.Text = '选择要交给助手的窗口'
  $form.Width = 520
  $form.Height = 420
  $form.StartPosition = 'CenterScreen'
  $form.TopMost = $true
  $list = New-Object System.Windows.Forms.ListBox
  $list.Dock = 'Fill'
  foreach ($row in $rows) {
    [void]$list.Items.Add(('{0}  (pid {1})' -f $row.Title, $row.Pid))
  }
  $list.SelectedIndex = 0
  $ok = New-Object System.Windows.Forms.Button
  $ok.Text = '附着'
  $ok.Dock = 'Bottom'
  $ok.Height = 36
  $state = @{ picked = $null }
  $ok.Add_Click({
    if ($list.SelectedIndex -ge 0) { $state.picked = $rows[$list.SelectedIndex] }
    $form.Close()
  })
  $list.Add_DoubleClick({
    if ($list.SelectedIndex -ge 0) {
      $state.picked = $rows[$list.SelectedIndex]
      $form.Close()
    }
  })
  $form.Controls.Add($list)
  $form.Controls.Add($ok)
  [void]$form.ShowDialog()
  if ($null -eq $state.picked) {
    Fail 'HostGuiPickerCancelled' '没有选出窗口。'
    return
  }
  $tick = [HostGuiNative]::LastInputTick()
  Emit @{
    ok = $true
    hwnd = [int64]$state.picked.Hwnd
    pid = [int]$state.picked.Pid
    title = [string]$state.picked.Title
    last_agent_tick = [int64]$tick
  }
} catch {
  Fail 'HostOpError' $_.Exception.Message
}
`;

const ACT_SCRIPT = `
${UIA_PREAMBLE}
function Get-WindowEl($hwnd) {
  [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$hwnd)
}
function RuntimeIdKey($el) {
  ($el.GetRuntimeId() | ForEach-Object { "$_" }) -join ','
}
function IsInteractive($el) {
  $t = $el.Current.ControlType
  $names = @(
    'Button','Edit','ComboBox','CheckBox','RadioButton','Hyperlink','TabItem',
    'ListItem','MenuItem','Slider','Spinner','TreeItem','SplitButton','DataItem',
    'Document','Custom'
  )
  return $names -contains $t.ProgrammaticName.Replace('ControlType.','')
}
function Walk($el, $depth, $maxDepth, $budget, $rows) {
  if ($depth -gt $maxDepth) { return }
  if ($rows.Count -ge $budget) { return }
  if (IsInteractive $el) {
    $name = [string]$el.Current.Name
    $ctype = $el.Current.ControlType.ProgrammaticName.Replace('ControlType.','')
    $rows.Add([pscustomobject]@{
      el = $el
      name = $name
      ctype = $ctype
      password = [bool]$el.Current.IsPassword
      enabled = [bool]$el.Current.IsEnabled
      key = RuntimeIdKey $el
    })
  }
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $child = $walker.GetFirstChild($el)
  while ($null -ne $child) {
    Walk $child ($depth+1) $maxDepth $budget $rows
    if ($rows.Count -ge $budget) { return }
    $child = $walker.GetNextSibling($child)
  }
}
function CaptureJpeg($hwnd) {
  try {
    Add-Type -AssemblyName System.Drawing
    $el = Get-WindowEl $hwnd
    $r = $el.Current.BoundingRectangle
    $w = [Math]::Max(1, [int]$r.Width)
    $h = [Math]::Max(1, [int]$r.Height)
    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    [void][HostGuiNative]::PrintWindow([IntPtr]$hwnd, $hdc, 2)
    $g.ReleaseHdc($hdc)
    $g.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
    $ep = New-Object System.Drawing.Imaging.EncoderParameters 1
    $ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality, [long]70)
    $bmp.Save($ms, $codec, $ep)
    $bmp.Dispose()
    return [Convert]::ToBase64String($ms.ToArray())
  } catch {
    return $null
  }
}
function FindByRuntimeKey($root, $want) {
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $stack = New-Object System.Collections.Stack
  $stack.Push($root)
  while ($stack.Count -gt 0) {
    $el = $stack.Pop()
    if ((RuntimeIdKey $el) -eq $want) { return $el }
    $child = $walker.GetFirstChild($el)
    while ($null -ne $child) {
      $stack.Push($child)
      $child = $walker.GetNextSibling($child)
    }
  }
  return $null
}
try {
  $op = [string]$guiIn.op
  $hwnd = [int64]$guiIn.hwnd
  $root = Get-WindowEl $hwnd
  if ($null -eq $root) {
    Fail 'HostGuiNotAttached' '窗口已经没了。'
    return
  }
  $title = [string]$root.Current.Name
  $pid = [int]$root.Current.ProcessId
  $tickNow = [HostGuiNative]::LastInputTick()
  $fgPid = [HostGuiNative]::FgPid()
  $lastAgent = [int64]$guiIn.last_agent_tick
  if ($op -ne 'snapshot') {
    if ($fgPid -eq $pid -and $tickNow -gt ($lastAgent + 50)) {
      Fail 'HostGuiUserAbort' '用户在该窗口里动了键鼠。'
      return
    }
  }
  if ($op -eq 'snapshot') {
    $rows = New-Object System.Collections.Generic.List[object]
    Walk $root 0 12 200 $rows
    if ($rows.Count -eq 0) {
      Fail 'HostGuiEmptyTree' '看不见该窗控件。'
      return
    }
    $lines = New-Object System.Collections.Generic.List[string]
    $refMap = @{}
    $i = 0
    foreach ($row in $rows) {
      $i++
      $ref = 'e' + $i
      $refMap[$ref] = $row.key
      $pw = ''
      if ($row.password) { $pw = ' password=true' }
      $en = ''
      if (-not $row.enabled) { $en = ' disabled' }
      $label = $row.name
      if ([string]::IsNullOrWhiteSpace($label)) { $label = $row.ctype }
      $lines.Add(('- {0} "{1}" [ref={2}]{3}{4}' -f $row.ctype.ToLower(), ($label -replace '"',''''), $ref, $pw, $en))
    }
    $jpeg = CaptureJpeg $hwnd
    $out = @{
      ok = $true
      title = $title
      pid = $pid
      hwnd = $hwnd
      snapshot_version = ([int]$guiIn.snapshot_version + 1)
      controls = ($lines -join "\`n")
      last_agent_tick = [int64]$tickNow
      refs = $refMap
    }
    if ($jpeg) { $out.jpeg_b64 = $jpeg }
    Emit $out
    return
  }
  $ref = [string]$guiIn.ref
  $want = [string]$guiIn.runtime_id
  if ([string]::IsNullOrWhiteSpace($want)) {
    Fail 'HostGuiRefStale' '缺少 runtime id。'
    return
  }
  $target = FindByRuntimeKey $root $want
  if ($null -eq $target) {
    Fail 'HostGuiRefStale' ("找不到 {0}" -f $ref)
    return
  }
  $cname = [string]$target.Current.Name
  if ($op -eq 'type' -and $target.Current.IsPassword) {
    Fail 'HostGuiPasswordDenied' '密码框。'
    return
  }
  if ($op -eq 'click') {
    $disabled = -not $target.Current.IsEnabled
    $matched = $false
    try {
      $inv = $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
      $inv.Invoke()
      $matched = $true
    } catch {
      try {
        $tog = $target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
        $tog.Toggle()
        $matched = $true
      } catch {
        try {
          $sel = $target.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
          $sel.Select()
          $matched = $true
        } catch {
          Fail 'HostGuiNoInvoke' '该控件不能点。'
          return
        }
      }
    }
    $jpeg = CaptureJpeg $hwnd
    $tick2 = [HostGuiNative]::LastInputTick()
    $out = @{
      ok = $true
      title = $title
      control_name = $cname
      last_agent_tick = [int64]$tick2
      clicked = @{
        was_disabled = [bool]$disabled
        matched = [bool]$matched
        name = $cname
      }
    }
    if ($jpeg) { $out.jpeg_b64 = $jpeg }
    Emit $out
    return
  }
  if ($op -eq 'type') {
    $text = [string]$guiIn.text
    try {
      $vp = $target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    } catch {
      Fail 'HostGuiNoValue' '该控件不能键入。'
      return
    }
    $vp.SetValue($text)
    $actual = [string]$vp.Current.Value
    $matched = $actual -eq $text
    $jpeg = CaptureJpeg $hwnd
    $tick2 = [HostGuiNative]::LastInputTick()
    $out = @{
      ok = $true
      title = $title
      control_name = $cname
      last_agent_tick = [int64]$tick2
      typed = @{
        requested_chars = $text.Length
        actual_chars = $actual.Length
        matched = [bool]$matched
      }
    }
    if ($jpeg) { $out.jpeg_b64 = $jpeg }
    Emit $out
    return
  }
  Fail 'HostOpError' ("unknown op {0}" -f $op)
} catch {
  Fail 'HostOpError' $_.Exception.Message
}
`;

function parseJsonObject(raw: string): Record<string, unknown> {
  if (!raw) return {};
  const start = raw.indexOf("{");
  const json = start >= 0 ? raw.slice(start) : raw;
  const parsed = JSON.parse(json) as unknown;
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : {};
}

export async function runGuiAttach(
  electronPid: number,
): Promise<Record<string, unknown>> {
  const payload = JSON.stringify({ electron_pid: electronPid });
  const script = `$InputJson = '${payload.replace(/'/g, "''")}'\n${ATTACH_SCRIPT}`;
  const raw = await runPowerShellEncoded(script, 180_000, {
    sta: true,
    interactive: true,
  });
  return parseJsonObject(raw);
}

export async function runGuiAct(
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const json = JSON.stringify(payload).replace(/'/g, "''");
  const script = `$InputJson = '${json}'\n${ACT_SCRIPT}`;
  const raw = await runPowerShellEncoded(script, 45_000, { sta: true });
  return parseJsonObject(raw);
}
