export function createDetailRoutes(params) {
  function path(endpoint, key, value) {
    const query = params();
    query.set(key, value);
    return `${endpoint}?${query}`;
  }

  return {
    turn: (sessionId, turnId) => {
      const query = params();
      query.set('session_id', sessionId);
      query.set('turn_id', turnId);
      return `/api/turn?${query}`;
    },
    session: sessionId => path('/api/session-detail', 'selected_session_id', sessionId),
    tool: toolName => path('/api/tool', 'tool_name', toolName),
    subagent: confidence => path('/api/subagent', 'confidence', confidence),
  };
}
