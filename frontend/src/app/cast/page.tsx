// Server entry for this route. The interactive app lives in the client component; the opt-out below silences the dev-only
// "could not validate instant navigation" notice (these pages are live, client-rendered apps).
import CastClient from "./cast-client";

export const instant = false;

export default function Page() {
  return <CastClient />;
}
