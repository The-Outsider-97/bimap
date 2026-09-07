"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  getCountries,
  getCountryCallingCode,
  parsePhoneNumberFromString,
  type CountryCode,
} from "libphonenumber-js/max";

export type PhoneValue = {
  country:
    CountryCode;

  callingCode:
    string;

  nationalNumber:
    string;

  e164:
    string;

  valid:
    boolean;

  numberType?:
    string;
};

type Props = {
  value: PhoneValue;

  onChange: (
    value: PhoneValue,
  ) => void;
};

function normalizeInput(
  value: string,
): string {
  return value
    .replace(/[^\d\s()-]/g, "")
    .slice(0, 32);
}

function resolveInitialCountry():
  CountryCode {
  if (
    typeof navigator !==
      "undefined"
  ) {
    const locale =
      navigator.language;

    const region =
      locale
        .split("-")[1]
        ?.toUpperCase();

    if (
      region &&
      getCountries().includes(
        region as CountryCode,
      )
    ) {
      return region as CountryCode;
    }
  }

  return "NL";
}

function buildPhoneValue(
  country:
    CountryCode,
  nationalNumber:
    string,
): PhoneValue {
  const callingCode =
    getCountryCallingCode(
      country,
    );

  const compact =
    nationalNumber.replace(
      /\D/g,
      "",
    );

  if (!compact) {
    return {
      country,
      callingCode,
      nationalNumber,
      e164: "",
      valid: false,
    };
  }

  const parsed =
    parsePhoneNumberFromString(
      `+${callingCode}${compact}`,
      country,
    );

  const possible =
    parsed?.isPossible() ??
    false;

  const valid =
    parsed?.isValid() ??
    false;

  const numberType =
    parsed?.getType();

  /*
   * SMS verification requires a
   * number that is not explicitly
   * classified as a fixed-line-only
   * or pager number.
   */
  const smsCapable =
    numberType !==
      "FIXED_LINE" &&
    numberType !==
      "PAGER";

  return {
    country,
    callingCode,
    nationalNumber,
    e164:
      parsed?.number ??
      `+${callingCode}${compact}`,
    valid:
      possible &&
      valid &&
      smsCapable,
    numberType,
  };
}

export function PhoneField({
  value,
  onChange,
}: Props) {
  const [
    touched,
    setTouched,
  ] =
    useState(false);

  useEffect(() => {
    if (!value.country) {
      const country =
        resolveInitialCountry();

      onChange(
        buildPhoneValue(
          country,
          "",
        ),
      );
    }
  }, [
    onChange,
    value.country,
  ]);

  const regionNames =
    useMemo(
      () =>
        new Intl.DisplayNames(
          ["en"],
          {
            type: "region",
          },
        ),
      [],
    );

  const countries =
    useMemo(
      () =>
        getCountries()
          .map(
            (country) => ({
              country,
              label:
                regionNames.of(
                  country,
                ) ??
                country,
              callingCode:
                getCountryCallingCode(
                  country,
                ),
            }),
          )
          .sort((a, b) =>
            a.label.localeCompare(
              b.label,
            ),
          ),
      [regionNames],
    );

  const invalid =
    touched &&
    value.nationalNumber
      .trim()
      .length > 0 &&
    !value.valid;

  return (
    <div className="account-field account-field--phone">
      <label
        htmlFor="signup-phone"
      >
        Phone number
        <span aria-hidden="true">
          *
        </span>
      </label>

      <div className="phone-field">
        <div className="phone-field__country">
          <select
            aria-label=
              "Country calling code"
            value={
              value.country ||
              "NL"
            }
            onChange={(
              event,
            ) => {
              const country =
                event.target
                  .value as CountryCode;

              onChange(
                buildPhoneValue(
                  country,
                  value.nationalNumber,
                ),
              );
            }}
          >
            {countries.map(
              ({
                country,
                label,
                callingCode,
              }) => (
                <option
                  key={country}
                  value={country}
                >
                  {country} +
                  {callingCode} ·{" "}
                  {label}
                </option>
              ),
            )}
          </select>
        </div>

        <div className="phone-field__number">
          <span>
            +
            {value.callingCode ||
              getCountryCallingCode(
                "NL",
              )}
          </span>

          <input
            id="signup-phone"
            type="tel"
            inputMode="tel"
            autoComplete="tel-national"
            value={
              value.nationalNumber
            }
            placeholder=
              "Complete phone number"
            aria-invalid={
              invalid
            }
            onBlur={() =>
              setTouched(true)
            }
            onChange={(
              event,
            ) => {
              const next =
                normalizeInput(
                  event.target
                    .value,
                );

              onChange(
                buildPhoneValue(
                  value.country ||
                    "NL",
                  next,
                ),
              );
            }}
          />
        </div>
      </div>

      <p
        className="account-field__hint"
        data-error={invalid}
      >
        {invalid
          ? "Enter a valid SMS-capable number for the selected country."
          : "Country-specific length and numbering rules are validated automatically."}
      </p>
    </div>
  );
}
