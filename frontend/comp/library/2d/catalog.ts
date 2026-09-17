import {
  apiRequest,
} from "@/lib/api";

import type {
  TwoDProduct,
} from "./types";


export async function getTwoDProducts():
  Promise<readonly TwoDProduct[]> {
  return apiRequest<readonly TwoDProduct[]>(
    "/storefront/2d",
  );
}
