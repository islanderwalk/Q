# 開一個 cmd 視窗(Windows Terminal)跑 ipconfig /all(只留有 IPv4 的重點列)、查公網 IP、顯示時間，然後把視窗截圖。不遮蔽。
param([string]$Out, [string]$Title = "IPEVID")
Add-Type -AssemblyName System.Drawing, System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h,int x,int y,int w,int ht,bool r);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
 public struct RECT { public int L, T, R, B; } }
"@
$cmdline = "title $Title & echo [%date% %time%] C:\^> ipconfig /all & ipconfig /all | findstr /i /c:`"adapter`" /c:`"Description`" /c:`"Physical Address`" /c:`"IPv4`" /c:`"Default Gateway`" /c:`"DNS Servers`" /c:`"DHCP Server`" & echo. & echo C:\^> curl https://api.ipify.org   (public IP) & curl -s https://api.ipify.org & echo. & echo. & echo [%date% %time%]"
Start-Process cmd.exe -ArgumentList "/k", $cmdline | Out-Null
$h = [IntPtr]::Zero
for ($i = 0; $i -lt 20 -and $h -eq [IntPtr]::Zero; $i++) {
  Start-Sleep 1
  $wt = Get-Process WindowsTerminal -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "$Title*" } | Select -First 1
  if ($wt) { $h = $wt.MainWindowHandle }
}
if ($h -eq [IntPtr]::Zero) { "no window handle"; exit 1 }
[W]::ShowWindow($h, 9) | Out-Null
[W]::MoveWindow($h, 20, 20, 1200, 900, $true) | Out-Null
[W]::SetForegroundWindow($h) | Out-Null
Start-Sleep 3
$r = New-Object W+RECT; [W]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.R - $r.L; $hh = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Out ${w}x${hh}"
