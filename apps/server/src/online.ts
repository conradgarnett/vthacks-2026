/** Best-effort connectivity check with a hard timeout. Nothing in SENSE requires the network. */
export async function checkOnline(timeoutMs = 2000): Promise<boolean> {
  if (process.env.SENSE_OFFLINE === '1') return false;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch('https://registry.npmjs.org/-/ping', { signal: ctl.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}
