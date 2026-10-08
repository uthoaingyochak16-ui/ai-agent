export const usd = (n: number) => (n === 0 ? "$0" : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);
export const num = (n: number) => new Intl.NumberFormat().format(Math.round(n));
export const when = (s?: string | null) => (s ? new Date(s).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "-");
export const ago = (s?: string | null) => {
  if (!s) return "-";
  const m = Math.round((Date.now() - new Date(s).getTime()) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
};
export const pct = (n: number) => `${Math.round(n * 100)}%`;
export const CHANNEL_LABEL: Record<string, string> = { messenger: "Messenger", instagram: "Instagram", whatsapp: "WhatsApp" };
