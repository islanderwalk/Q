# Open pcapng in real Wireshark GUI with display filter, then window screenshot (no masking).
param([string]$Pcap, [string]$Filter, [string]$Out)
Add-Type -AssemblyName System.Drawing, System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W2 { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h,int x,int y,int w,int ht,bool r);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
 public struct RECT { public int L, T, R, B; } }
"@
$p = Start-Process "C:\Program Files\Wireshark\Wireshark.exe" -ArgumentList @("-r", $Pcap, "-Y", $Filter, "-o", "gui.geometry_main_maximized:false") -PassThru
$h = [IntPtr]::Zero
for ($i = 0; $i -lt 40 -and $h -eq [IntPtr]::Zero; $i++) {
  Start-Sleep 1
  $w = Get-Process Wireshark -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "*tls_capture*" } | Select -First 1
  if ($w) { $h = $w.MainWindowHandle }
}
if ($h -eq [IntPtr]::Zero) { "no window"; Get-Process Wireshark | Select MainWindowTitle; exit 1 }
[W2]::ShowWindow($h, 9) | Out-Null
[W2]::MoveWindow($h, 10, 10, 1500, 760, $true) | Out-Null
[W2]::SetForegroundWindow($h) | Out-Null
Start-Sleep 4
$r = New-Object W2+RECT; [W2]::GetWindowRect($h, [ref]$r) | Out-Null
$ww = $r.R - $r.L; $hh = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $ww, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Out ${ww}x${hh} title=" + (Get-Process -Id $p.Id -ErrorAction SilentlyContinue).MainWindowTitle

