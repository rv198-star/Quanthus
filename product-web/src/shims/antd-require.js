export function installAntdRequireShim () {
  if (typeof window === 'undefined' || typeof window.require === 'function') return
  window.require = (name) => {
    if (name === 'enquire.js') {
      return { register: () => {}, unregister: () => {} }
    }
    if (name === 'mutationobserver-shim') return window.MutationObserver
    return {}
  }
}
