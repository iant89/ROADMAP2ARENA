// Browser (OS) notifications. The on/off preference is per browser (localStorage), because the
// permission is granted per browser too. Shown only while the app tab is hidden or unfocused;
// in-app toasts cover the visible case.
const KEY = 'r2a.browserNotifications'

export function browserSupported() {
  return typeof window !== 'undefined' && 'Notification' in window
}

export function browserPermission() {
  return browserSupported() ? window.Notification.permission : 'unsupported'
}

export function browserPref() {
  try { return window.localStorage.getItem(KEY) === '1' } catch { return false }
}

export function setBrowserPref(on) {
  try { window.localStorage.setItem(KEY, on ? '1' : '0') } catch { /* private mode */ }
}

// Asks for permission when needed. Resolves to the resulting permission.
export async function enableBrowser() {
  if (!browserSupported()) return 'unsupported'
  let perm = window.Notification.permission
  if (perm === 'default') perm = await window.Notification.requestPermission()
  setBrowserPref(perm === 'granted')
  return perm
}

export function showBrowser(n, onClick) {
  if (!browserPref() || browserPermission() !== 'granted') return false
  if (document.visibilityState === 'visible' && document.hasFocus()) return false
  try {
    const note = new window.Notification('ROADMAP2ARENA', { body: n.message, tag: n.id })
    note.onclick = () => { window.focus(); onClick?.(); note.close() }
    return true
  } catch {
    return false
  }
}
