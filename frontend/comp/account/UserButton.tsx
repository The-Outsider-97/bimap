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

  if (
    status === "signed-in" &&
    account
  ) {
    return (
      <Link
        href="/account"
        className="user-access-button"
        aria-label={
          `Open ${account.username}'s account`
        }
      >
        <span className="user-access-button__avatar">
          <img
            src={imageSource}
            alt=""
          />
        </span>

        <span className="user-access-button__copy">
          <small>
            Account
          </small>

          <strong>
            {account.username}
          </strong>
        </span>
      </Link>
    );
  }

  return (
    <button
      type="button"
      className="user-access-button"
      data-loading={
        status === "loading"
      }
      onClick={() =>
        openAuth("login")
      }
      aria-label=
        "Log in or create a BIMAP account"
    >
      <span className="user-access-button__avatar">
        <img
          src="/default-user.png"
          alt=""
        />
      </span>

      <span className="user-access-button__copy">
        <small>
          BIMAP account
        </small>

        <strong>
          {status === "loading"
            ? "Checking..."
            : "Log in / Sign up"}
        </strong>
      </span>
    </button>
  );
}
