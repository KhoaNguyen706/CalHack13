// Where the page reaches the backends: the same address as the page (Vite proxies these paths), unless a
// deploy points elsewhere with VITE_* variables.
export const API = import.meta.env.VITE_COORDINATOR_URL || `${location.origin}/api`;
export const AGENTS = import.meta.env.VITE_AGENTS_URL || `${location.origin}/agents-api`;

// Set once this browser has opened a challenge, so the chat stops sending you back to it.
export const openedKey = (matchId: string) => `krew-opened-${matchId}`;
