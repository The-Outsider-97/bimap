"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  AuthModal,
} from "./AuthModal";

import {
  getCurrentAccount,
  logout as logoutRequest,
  type AccountProfile,
  type AuthMode,
} from "@/lib/account";

import {
  BimapApiError,
} from "@/lib/api";

type AccountStatus =
  | "loading"
  | "signed-in"
  | "signed-out";

type AccountContextValue = {
  account:
    AccountProfile | null;

  status:
    AccountStatus;

  authOpen:
    boolean;

  authMode:
    AuthMode;

  openAuth: (
    mode?: AuthMode,
  ) => void;

  closeAuth: () => void;

  refresh: () =>
    Promise<void>;

  logout: () =>
    Promise<void>;
};

const AccountContext =
  createContext<
    AccountContextValue | null
  >(null);

export function AccountProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [
    account,
    setAccount,
  ] =
    useState<
      AccountProfile | null
    >(null);

  const [
    status,
    setStatus,
  ] =
    useState<AccountStatus>(
      "loading",
    );

  const [
    authOpen,
    setAuthOpen,
  ] =
    useState(false);

  const [
    authMode,
    setAuthMode,
  ] =
    useState<AuthMode>(
      "login",
    );

  const refresh =
    useCallback(
      async () => {
        setStatus("loading");

        try {
          const profile =
            await getCurrentAccount();

          setAccount(profile);
          setStatus(
            "signed-in",
          );
        } catch (error) {
          if (
            error instanceof
              BimapApiError &&
            error.status === 401
          ) {
            setAccount(null);

            setStatus(
              "signed-out",
            );

            return;
          }

          /*
           * Do not fabricate a signed-in
           * state when the API is
           * unavailable.
           */
          setAccount(null);

          setStatus(
            "signed-out",
          );
        }
      },
      [],
    );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    document.body.classList.toggle(
      "is-account-modal-open",
      authOpen,
    );

    return () => {
      document.body.classList.remove(
        "is-account-modal-open",
      );
    };
  }, [authOpen]);

  const openAuth =
    useCallback(
      (
        mode:
          AuthMode = "login",
      ) => {
        setAuthMode(mode);
        setAuthOpen(true);
      },
      [],
    );

  const closeAuth =
    useCallback(() => {
      setAuthOpen(false);
    }, []);

  const logout =
    useCallback(
      async () => {
        await logoutRequest();

        setAccount(null);

        setStatus(
          "signed-out",
        );
      },
      [],
    );

  const value =
    useMemo<
      AccountContextValue
    >(
      () => ({
        account,
        status,
        authOpen,
        authMode,
        openAuth,
        closeAuth,
        refresh,
        logout,
      }),
      [
        account,
        authMode,
        authOpen,
        closeAuth,
        logout,
        openAuth,
        refresh,
        status,
      ],
    );

  return (
    <AccountContext.Provider
      value={value}
    >
      {children}

      <AuthModal
        open={authOpen}
        initialMode={authMode}
        onClose={closeAuth}
        onAuthenticated={
          refresh
        }
      />
    </AccountContext.Provider>
  );
}

export function useAccount() {
  const context =
    useContext(
      AccountContext,
    );

  if (!context) {
    throw new Error(
      "useAccount must be used inside AccountProvider.",
    );
  }

  return context;
}
