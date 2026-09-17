import {
  apiRequest,
} from "@/lib/api";

import type {
  ThreeDProduct,
} from "./types";


export async function getThreeDProducts():
  Promise<readonly ThreeDProduct[]> {

  try {
    return await apiRequest<
      readonly ThreeDProduct[]
    >(
      "/storefront/3d",
    );
  } catch (error) {
    /*
     * The library itself must remain renderable when the backend catalog
     * is temporarily unavailable.  The backend remains authoritative and
     * the failure is still visible in the server log.
     */
    console.error(
      "[BIMAP] 3D storefront catalog request failed.",
      error,
    );

    return [];
  }
}
