/** Full-page navigation to the provider's checkout page (kept separate so tests can stub it). */
export function goToProvider(url: string): void {
  window.location.assign(url);
}
