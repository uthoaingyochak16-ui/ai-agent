/** Embedded mode: the assistant runs inside the main application, which owns the one and only navigation bar.
 *  Set at build time:
 *    VITE_EMBEDDED=true        no side bar, no mobile header, no theme/sign-out buttons (the main app has them)
 *    VITE_BASE_PATH=/assistant the URL prefix the main app serves the assistant under (default: "/")
 *  Left unset, the assistant looks and works exactly as a stand-alone dashboard. */
export const EMBEDDED = import.meta.env.VITE_EMBEDDED === "true";

/** Router basename without a trailing slash; "" when served from the root. */
export const BASE_PATH: string = ((import.meta.env.VITE_BASE_PATH as string | undefined) ?? "").replace(/\/+$/, "");
