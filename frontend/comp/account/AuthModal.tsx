"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  getApiErrorMessage,
  getSignupPasswordError,
  login,
  resendSignupCodes,
  SIGNUP_PASSWORD_MIN_LENGTH,
  signUp,
  verifySignup,
  type AuthMode,
} from "@/lib/account";

import {
  PhoneField,
  type PhoneValue,
} from "./PhoneField";

type Step =
  | "login"
  | "signup"
  | "verify";

type Props = {
  open: boolean;

  initialMode:
    AuthMode;

  onClose: () => void;

  onAuthenticated: () =>
    Promise<void>;
};

const emptyPhone:
  PhoneValue = {
    country: "NL",
    callingCode: "31",
    nationalNumber: "",
    e164: "",
    valid: false,
  };

export function AuthModal({
  open,
  initialMode,
  onClose,
  onAuthenticated,
}: Props) {
  const [
    step,
    setStep,
  ] =
    useState<Step>(
      initialMode,
    );

  const [
    busy,
    setBusy,
  ] =
    useState(false);

  const [
    message,
    setMessage,
  ] =
    useState("");

  const [
    pendingUsername,
    setPendingUsername,
  ] =
    useState("");

  const [
    signup,
    setSignup,
  ] =
    useState({
      name: "",
      surname: "",
      occupation: "",
      business: "",
      username: "",
      email: "",
      password: "",
      confirmPassword: "",
    });

  const [
    phone,
    setPhone,
  ] =
    useState<PhoneValue>(
      emptyPhone,
    );

  const [
    loginForm,
    setLoginForm,
  ] =
    useState({
      username: "",
      password: "",
    });

  const [
    verifyForm,
    setVerifyForm,
  ] =
    useState({
      emailCode: "",
      smsCode: "",
    });
  const [
    smsRequired,
    setSmsRequired,
  ] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }

    setStep(initialMode);
    setMessage("");
  }, [
    initialMode,
    open,
  ]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const onKeyDown = (
      event: KeyboardEvent,
    ) => {
      if (
        event.key ===
        "Escape"
      ) {
        onClose();
      }
    };

    document.addEventListener(
      "keydown",
      onKeyDown,
    );

    return () => {
      document.removeEventListener(
        "keydown",
        onKeyDown,
      );
    };
  }, [
    onClose,
    open,
  ]);

  const title =
    useMemo(() => {
      if (
        step === "signup"
      ) {
        return "Create your BIMAP account";
      }

      if (
        step === "verify"
      ) {
        return "Verify your account";
      }

      return "Welcome back";
    }, [step]);

  if (!open) {
    return null;
  }

  const submitLogin =
    async (
      event:
        React.FormEvent,
    ) => {
      event.preventDefault();

      setBusy(true);
      setMessage("");

      try {
        await login({
          username:
            loginForm.username
              .trim(),

          password:
            loginForm.password,
        });

        await onAuthenticated();

        onClose();
      } catch (error) {
        setMessage(
          getApiErrorMessage(
            error,
          ),
        );
      } finally {
        setBusy(false);
      }
    };

  const submitSignup =
    async (
      event:
        React.FormEvent,
    ) => {
      event.preventDefault();

      setMessage("");
      const passwordError =
        getSignupPasswordError(
          signup.password,
        );

      if (passwordError) {
        setMessage(
          passwordError,
        );

        return;
      }

      if (
        signup.password !==
        signup.confirmPassword
      ) {
        setMessage(
          "Password and confirmation password do not match.",
        );

        return;
      }

      if (!phone.valid) {
        setMessage(
          "Enter a valid SMS-capable phone number for the selected country.",
        );

        return;
      }

      setBusy(true);

      try {
        const result =
          await signUp({
            name:
              signup.name
                .trim(),

            surname:
              signup.surname
                .trim(),

            occupation:
              signup.occupation
                .trim() ||
              undefined,

            business:
              signup.business
                .trim() ||
              undefined,

            country:
              phone.country,

            phoneE164:
              phone.e164,

            username:
              signup.username
                .trim(),

            email:
              signup.email
                .trim(),

            password:
              signup.password,
          });

        setPendingUsername(
          result.username,
        );

        setSmsRequired(
          Boolean(
            result.phoneMasked,
          ),
        );

        setStep(
          "verify",
        );

        setMessage(
          result.phoneMasked
            ? "Verification codes were requested for your email address and mobile number."
            : "A verification code was sent to your email address.",
        );
      } catch (error) {
        setMessage(
          getApiErrorMessage(
            error,
          ),
        );
      } finally {
        setBusy(false);
      }
    };

  const submitVerification =
    async (
      event:
        React.FormEvent,
    ) => {
      event.preventDefault();

      setMessage("");

      const sms =
        verifyForm.smsCode
          .replace(
            /\D/g,
            "",
          );

      if (
        smsRequired &&
        sms.length !== 6
      ) {
        setMessage(
          "The SMS verification code must contain exactly six digits.",
        );

        return;
      }

      if (
        !verifyForm.emailCode
          .trim()
      ) {
        setMessage(
          "Enter the verification code sent to your email address.",
        );

        return;
      }

      setBusy(true);

      try {
        await verifySignup({
          username:
            pendingUsername,

          emailCode:
            verifyForm
              .emailCode
              .trim(),

          smsCode:
            smsRequired
              ? sms
              : undefined,
        });

        await onAuthenticated();

        onClose();
      } catch (error) {
        setMessage(
          getApiErrorMessage(
            error,
          ),
        );
      } finally {
        setBusy(false);
      }
    };

  return (
    <div
      className="account-modal-backdrop"
      onMouseDown={onClose}
    >
      <section
        className="account-auth-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby=
          "account-auth-title"
        onMouseDown={(
          event,
        ) =>
          event.stopPropagation()
        }
      >
        <header className="account-auth-modal__header">
          <div>
            <p>
              BIMAP account
            </p>

            <h2
              id=
                "account-auth-title"
            >
              {title}
            </h2>
          </div>

          <button
            type="button"
            className="account-modal-close"
            aria-label=
              "Close account dialog"
            onClick={onClose}
          >
            <span />
            <span />
          </button>
        </header>

        <div className="account-auth-modal__body">
          {message ? (
            <div
              className="account-auth-message"
              role="status"
            >
              {message}
            </div>
          ) : null}

          {step === "login" ? (
            <form
              className="account-form"
              onSubmit={
                submitLogin
              }
            >
              <div className="account-field">
                <label
                  htmlFor=
                    "login-username"
                >
                  Username
                </label>

                <input
                  id=
                    "login-username"
                  autoComplete=
                    "username"
                  required
                  value={
                    loginForm.username
                  }
                  onChange={(
                    event,
                  ) =>
                    setLoginForm(
                      (
                        current,
                      ) => ({
                        ...current,
                        username:
                          event.target
                            .value,
                      }),
                    )
                  }
                />
              </div>

              <div className="account-field">
                <label
                  htmlFor=
                    "login-password"
                >
                  Password
                </label>

                <input
                  id=
                    "login-password"
                  type="password"
                  autoComplete=
                    "current-password"
                  required
                  value={
                    loginForm.password
                  }
                  onChange={(
                    event,
                  ) =>
                    setLoginForm(
                      (
                        current,
                      ) => ({
                        ...current,
                        password:
                          event.target
                            .value,
                      }),
                    )
                  }
                />
              </div>

              <button
                type="submit"
                className="account-submit"
                disabled={busy}
              >
                <span>
                  {busy
                    ? "Signing in..."
                    : "Log in"}
                </span>

                <span
                  aria-hidden="true"
                >
                  ↗
                </span>
              </button>

              <button
                type="button"
                className="account-form-switch"
                onClick={() => {
                  setMessage("");
                  setStep(
                    "signup",
                  );
                }}
              >
                No account yet?
                Create one
              </button>
            </form>
          ) : null}

          {step === "signup" ? (
            <form
              className="
                account-form
                account-form--signup
              "
              onSubmit={
                submitSignup
              }
            >
              <div className="account-form__grid">
                <div className="account-field">
                  <label
                    htmlFor=
                      "signup-name"
                  >
                    Name
                  </label>

                  <input
                    id=
                      "signup-name"
                    autoComplete=
                      "given-name"
                    required
                    value={
                      signup.name
                    }
                    onChange={(
                      event,
                    ) =>
                      setSignup(
                        (
                          current,
                        ) => ({
                          ...current,
                          name:
                            event.target
                              .value,
                        }),
                      )
                    }
                  />
                </div>

                <div className="account-field">
                  <label
                    htmlFor=
                      "signup-surname"
                  >
                    Surname
                  </label>

                  <input
                    id=
                      "signup-surname"
                    autoComplete=
                      "family-name"
                    required
                    value={
                      signup.surname
                    }
                    onChange={(
                      event,
                    ) =>
                      setSignup(
                        (
                          current,
                        ) => ({
                          ...current,
                          surname:
                            event.target
                              .value,
                        }),
                      )
                    }
                  />
                </div>

                <div className="account-field">
                  <label
                    htmlFor=
                      "signup-occupation"
                  >
                    Occupation
                    <small>
                      optional
                    </small>
                  </label>

                  <input
                    id=
                      "signup-occupation"
                    value={
                      signup.occupation
                    }
                    onChange={(
                      event,
                    ) =>
                      setSignup(
                        (
                          current,
                        ) => ({
                          ...current,
                          occupation:
                            event.target
                              .value,
                        }),
                      )
                    }
                  />
                </div>

                <div className="account-field">
                  <label
                    htmlFor=
                      "signup-business"
                  >
                    Business
                    <small>
                      optional
                    </small>
                  </label>

                  <input
                    id=
                      "signup-business"
                    autoComplete=
                      "organization"
                    value={
                      signup.business
                    }
                    onChange={(
                      event,
                    ) =>
                      setSignup(
                        (
                          current,
                        ) => ({
                          ...current,
                          business:
                            event.target
                              .value,
                        }),
                      )
                    }
                  />
                </div>

                <div className="account-form__full">
                  <PhoneField
                    value={phone}
                    onChange={
                      setPhone
                    }
                  />
                </div>

                <div className="account-field">
                  <label
                    htmlFor=
                      "signup-username"
                  >
                    Username
                  </label>

                  <input
                    id=
                      "signup-username"
                    autoComplete=
                      "username"
                    required
                    value={
                      signup.username
                    }
                    onChange={(
                      event,
                    ) =>
                      setSignup(
                        (
                          current,
                        ) => ({
                          ...current,
                          username:
                            event.target
                              .value,
                        }),
                      )
                    }
                  />
                </div>

                <div className="account-field">
                  <label
                    htmlFor=
                      "signup-email"
                  >
                    Email
                  </label>

                  <input
                    id=
                      "signup-email"
                    type="email"
                    autoComplete=
                      "email"
                    required
                    value={
                      signup.email
                    }
                    onChange={(
                      event,
                    ) =>
                      setSignup(
                        (
                          current,
                        ) => ({
                          ...current,
                          email:
                            event.target
                              .value,
                        }),
                      )
                    }
                  />
                </div>

                <div className="account-field">
                  <label
                    htmlFor=
                      "signup-password"
                  >
                    Password
                  </label>

                  <input
                    id=
                      "signup-password"
                    type="password"
                    autoComplete=
                      "new-password"
                    required
                    value={
                      signup.password
                    }
                    onChange={(
                      event,
                    ) =>
                      setSignup(
                        (
                          current,
                        ) => ({
                          ...current,
                          password:
                            event.target
                              .value,
                        }),
                      )
                    }
                  />
                </div>

                <div className="account-field">
                  <label
                    htmlFor=
                      "signup-confirm-password"
                  >
                    Confirm
                    password
                  </label>

                  <input
                    id=
                      "signup-confirm-password"
                    type="password"
                    autoComplete=
                      "new-password"
                    required
                    value={
                      signup
                        .confirmPassword
                    }
                    onChange={(
                      event,
                    ) =>
                      setSignup(
                        (
                          current,
                        ) => ({
                          ...current,
                          confirmPassword:
                            event.target
                              .value,
                        }),
                      )
                    }
                  />
                </div>
              </div>

              <button
                type="submit"
                className="account-submit"
                disabled={busy}
              >
                <span>
                  {busy
                    ? "Creating account..."
                    : "Create account"}
                </span>

                <span
                  aria-hidden="true"
                >
                  ↗
                </span>
              </button>

              <button
                type="button"
                className="account-form-switch"
                onClick={() => {
                  setMessage("");
                  setStep(
                    "login",
                  );
                }}
              >
                Already have an
                account? Log in
              </button>
            </form>
          ) : null}

          {step === "verify" ? (
            <form
              className="account-form"
              onSubmit={
                submitVerification
              }
            >
              <div className="account-verification-intro">
                <span>
                  01
                </span>

                <div>
                  <h3>
                    {smsRequired
                      ? "Confirm both channels."
                      : "Confirm your email."}
                  </h3>

                  <p>
                    {smsRequired
                      ? "Enter the verification code sent to your email address and the six-digit code sent by SMS."
                      : "Enter the verification code sent to your email address."}
                  </p>
                </div>
              </div>

              <div className="account-field">
                <label
                  htmlFor=
                    "verify-email-code"
                >
                  Email
                  verification code
                </label>

                <input
                  id=
                    "verify-email-code"
                  autoComplete=
                    "one-time-code"
                  required
                  value={
                    verifyForm
                      .emailCode
                  }
                  onChange={(
                    event,
                  ) =>
                    setVerifyForm(
                      (
                        current,
                      ) => ({
                        ...current,
                        emailCode:
                          event.target
                            .value,
                      }),
                    )
                  }
                />
              </div>

            {smsRequired ? (
              <div className="account-field">
                <label
                  htmlFor=
                    "verify-sms-code"
                >
                  SMS code
                </label>

                <input
                  id=
                    "verify-sms-code"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  autoComplete=
                    "one-time-code"
                  required
                  value={
                    verifyForm
                      .smsCode
                  }
                  onChange={(
                    event,
                  ) =>
                    setVerifyForm(
                      (
                        current,
                      ) => ({
                        ...current,
                        smsCode:
                          event.target
                            .value
                            .replace(
                              /\D/g,
                              "",
                            )
                            .slice(
                              0,
                              6,
                            ),
                      }),
                    )
                  }
                />
              </div>
            ) : null}

              <button
                type="submit"
                className="account-submit"
                disabled={busy}
              >
                <span>
                  {busy
                    ? "Verifying..."
                    : "Verify account"}
                </span>

                <span
                  aria-hidden="true"
                >
                  ✓
                </span>
              </button>

              <button
                type="button"
                className="account-form-switch"
                disabled={busy}
                onClick={async () => {
                  if (
                    !pendingUsername
                  ) {
                    return;
                  }

                  setBusy(true);
                  setMessage("");

                  try {
                    await resendSignupCodes(
                      pendingUsername,
                    );

                    setMessage(
                      "New verification codes were requested.",
                    );
                  } catch (error) {
                    setMessage(
                      getApiErrorMessage(
                        error,
                      ),
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {smsRequired
                  ? "Resend both verification codes"
                  : "Resend verification code"}
              </button>
            </form>
          ) : null}
        </div>

        <footer className="account-auth-modal__footer">
          <span>
            Authentication and
            verification are handled
            server-side.
          </span>

          <span>
            BIMAP / R3D
          </span>
        </footer>
      </section>
    </div>
  );
}
