import type { Metadata } from "next";

import ThreeDLibraryPage from "@/comp/library/3d/page";
import { getThreeDProducts } from "@/comp/library/3d/catalog";

export const metadata: Metadata = {
  title: "3D Models & Scenes",
  description:
    "Browse BIMAP Revit, 3ds Max, STL, 3D DWG, OBJ and GLB products.",
};

export default async function Page() {
  const products = await getThreeDProducts();
  return <ThreeDLibraryPage products={products} />;
}
