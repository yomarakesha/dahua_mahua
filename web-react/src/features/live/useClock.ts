import { useEffect, useState } from "react";

/** A ticking clock that updates every second; formatted HH:MM:SS (24h). */
export function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now.toLocaleTimeString("en-GB", { hour12: false });
}
