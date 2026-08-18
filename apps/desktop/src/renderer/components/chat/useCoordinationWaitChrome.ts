import { useEffect, useState } from "react";

/** Seconds since ``startedAt`` ms, ticking once per second while active. */
export function useElapsedSince(startedAt: number | null | undefined): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (startedAt == null) {
      setElapsed(0);
      return;
    }
    const tick = () =>
      setElapsed(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return elapsed;
}
