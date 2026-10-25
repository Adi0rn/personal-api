"""Submit a Windows desktop toast without a browser or third-party package."""
import base64
import os
from pathlib import Path
import subprocess
from xml.etree import ElementTree as ET


def notify(title, message):
    if os.name != 'nt':
        raise RuntimeError('Desktop notifications require Windows.')
    toast = ET.Element('toast', duration='short')
    binding = ET.SubElement(ET.SubElement(toast, 'visual'), 'binding', template='ToastGeneric')
    ET.SubElement(binding, 'text').text = title[:200]
    ET.SubElement(binding, 'text').text = message[:1000]
    payload = base64.b64encode(ET.tostring(toast, encoding='utf-8')).decode('ascii')
    # User text is XML-escaped and base64 data, never PowerShell source.
    script = r'''$ErrorActionPreference='Stop'
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotifier,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] | Out-Null
$xml=New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PAYLOAD')))
$notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell')
if ($null -ne $notifier.Setting -and $notifier.Setting.ToString() -ne 'Enabled') { throw 'Notifications are disabled' }
$toast=[Windows.UI.Notifications.ToastNotification]::new($xml)
$toast.ExpirationTime=[DateTimeOffset]::Now.AddMinutes(5)
$notifier.Show($toast)
'''.replace('PAYLOAD', payload)
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
            '-EncodedCommand', base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Windows did not accept the notification. Check notification settings for Windows PowerShell and Do not disturb.')
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Could not submit the Windows notification. Try again.') from None
