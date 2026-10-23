import * as React from 'react';

const MOBILE_BREAKPOINT = 768;
// Matches the previous effect: resize notifications are debounced so a
// drag across the breakpoint does not re-render on every pixel.
const RESIZE_DEBOUNCE_MS = 150;

function subscribeToMobileMedia(onStoreChange: () => void): () => void {
  const mediaQuery = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const onChange = (): void => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(onStoreChange, RESIZE_DEBOUNCE_MS);
  };
  mediaQuery.addEventListener('change', onChange);
  return () => {
    mediaQuery.removeEventListener('change', onChange);
    clearTimeout(timeoutId);
  };
}

function readIsMobile(): boolean {
  return window.innerWidth < MOBILE_BREAKPOINT;
}

function readIsMobileOnServer(): boolean {
  return false;
}

/**
 * Viewport flag for layouts that branch below 768px.
 *
 * Uses `useSyncExternalStore` so the width is read from the browser without
 * `setState` inside an effect. eslint-config-next 16.4 rejects that pattern
 * (`react-hooks/set-state-in-effect`). The server snapshot stays false, which
 * is the same first paint as the previous `undefined` state.
 *
 * @returns Whether the current viewport is below the mobile breakpoint.
 * @example
 * const isMobile = useIsMobile()
 */
export function useIsMobile(): boolean {
  return React.useSyncExternalStore(subscribeToMobileMedia, readIsMobile, readIsMobileOnServer);
}
