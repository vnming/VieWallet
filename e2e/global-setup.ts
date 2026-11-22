import { resetRateLimits } from "./tests/support";

export default function globalSetup(): void {
  resetRateLimits();
}
