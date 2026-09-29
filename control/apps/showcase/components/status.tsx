type State = 'Live' | 'Landing' | 'Built, off' | 'Held' | 'Unresolved' | 'Test data' | 'Coming';
export function Status(props: { state: Exclude<State, 'Coming'> } | { state: 'Coming'; target: string }) {
  const tone = props.state === 'Live' ? 'live' : props.state === 'Coming' ? 'coming' : 'held';
  return <span className={`status ${tone}`}><span aria-hidden="true" className="dot" />{props.state}{props.state === 'Coming' ? ` · ${props.target}` : ''}</span>;
}
