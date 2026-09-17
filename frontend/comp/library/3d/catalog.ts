import {
  apiRequest,
} from "@/lib/api";

import type {
  ThreeDProduct,
} from "./types";


export async function getThreeDProducts():
  Promise<readonly ThreeDProduct[]> {
  return apiRequest<readonly ThreeDProduct[]>(
    "/storefront/3d",
  );
}
