
export default function Loading() {
  return (
    <div className="route-loader" role="status" aria-live="polite">
      <div className="route-loader__orb" aria-hidden="true">
        <span />
        <span />
        <img src="/remy3design-mark.png" alt="" />
      </div>
      <p>Loading BIMAP</p>
    </div>
  );
}
