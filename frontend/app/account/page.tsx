import type {
  Metadata,
} from "next";

import {
  AccountPage,
} from "@/comp/account/AccountPage";

export const metadata:
  Metadata = {
    title:
      "Account",
    description:
      "Manage your BIMAP profile, audit activity, digital purchases and subscription plan.",
  };

export default function Page() {
  return <AccountPage />;
}
