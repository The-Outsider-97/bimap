import type { Metadata } from "next";

import TwoDLibraryPage from "@/comp/library/2d/page";
import { getTwoDProducts } from "@/comp/library/2d/catalog";

export const metadata: Metadata = {
  title: "2D DWG Library",
  description:
    "Browse BIMAP 2D DWG products and coordinated drawing packs.",
};

export default async function Page() {
  const products = await getTwoDProducts();
  return <TwoDLibraryPage products={products} />;
}
