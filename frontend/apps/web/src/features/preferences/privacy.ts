import { useMe } from "@viewallet/api-client";

/** Privacy mode (account setting): balances and amounts start hidden. */
export function usePrivacyMode(): boolean {
  return useMe().data?.privacy_mode ?? false;
}
