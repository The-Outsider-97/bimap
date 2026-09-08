"use client";

import Link from "next/link";

import {
  useAccount,
} from "./AccountProvider";


export function UserButton() {
  const {
    account,
    status,
    openAuth,
  } = useAccount();


  const imageSource =
    account?.avatarUrl ||
    "/default-user.png";


  /*
   * Authenticated:
   * clicking the circular avatar
   * opens the account page.
   */
  if (
    status === "signed-in" &&
    account
  ) {
    return (
      <Link
        href="/account"

        className="user-button"

        aria-label={
          `Open ${account.username}'s BIMAP account`
        }

        title={
          `Account: ${account.username}`
        }
      >
        <img
          src={imageSource}
          alt=""
        />
      </Link>
    );
  }


  /*
   * Signed out:
   * clicking the same circular
   * control opens login/signup.
   */
  return (
    <button
      type="button"

      className="user-button"

      data-loading={
        status === "loading"
      }

      onClick={() => {
        if (
          status !== "loading"
        ) {
          openAuth("login");
        }
      }}

      aria-label={
        status === "loading"
          ? "Checking BIMAP account"
          : "Log in or create a BIMAP account"
      }

      title={
        status === "loading"
          ? "Checking account..."
          : "Log in / Sign up"
      }
    >
      <img
        src="/default-user.png"
        alt=""
      />
    </button>
  );
}