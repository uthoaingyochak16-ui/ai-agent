import { useEffect, useRef } from "react";

/** Run a save only after the user has stopped editing, never on the initial render. */
export function useDebouncedAutosave(value: unknown, enabled: boolean, save: () => void, delay = 750) {
  const ready = useRef(false);
  const saveRef = useRef(save);
  saveRef.current = save;

  useEffect(() => {
    if (!enabled) {
      ready.current = false;
      return;
    }
    if (!ready.current) {
      ready.current = true;
      return;
    }
    const timer = window.setTimeout(() => saveRef.current(), delay);
    return () => window.clearTimeout(timer);
  }, [value, enabled, delay]);
}
