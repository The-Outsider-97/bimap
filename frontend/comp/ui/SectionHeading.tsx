type Props = {
  eyebrow: string;
  title: string;
  description?: string;
};

export function SectionHeading({
  eyebrow,
  title,
  description,
}: Props) {
  return (
    <div className="section-heading">
      <p className="eyebrow">
        <span aria-hidden="true">●</span>
        {eyebrow}
      </p>

      <h2>{title}</h2>

      {description ? (
        <p className="section-heading__copy">{description}</p>
      ) : null}
    </div>
  );
}
