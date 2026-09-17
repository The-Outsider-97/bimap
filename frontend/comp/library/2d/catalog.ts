import {
  apiRequest,
} from "@/lib/api";

import type {
  TwoDProduct,
} from "./types";


export async function getTwoDProducts():
  Promise<readonly TwoDProduct[]> {

  try {
    return await apiRequest<
      readonly TwoDProduct[]
    >(
      "/storefront/2d",
    );
  } catch (error) {
    /*
     * The library itself must remain renderable when the backend catalog
     * is temporarily unavailable.  The backend remains authoritative and
     * the failure is still visible in the server log.
     */
    console.error(
      "[BIMAP] 2D storefront catalog request failed.",
      error,
    );

    return [];
  }
}
