// App deliberately lazy-loads screens. Transform these before interaction
// assertions so a cold TypeScript/AntD transform does not consume their timeout.
// This only prepares test modules; it does not render, mock requests or measure
// application loading performance.
export async function preloadScreens() {
  await Promise.all([import('./Workspace'), import('./auth/AccountScreens'), import('./crews/InvitationScreens')])
}
