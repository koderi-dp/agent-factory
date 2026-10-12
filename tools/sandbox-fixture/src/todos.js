export function addTodo(todos, title) {
  const text = title.trim()
  if (!text) return todos
  return [...todos, { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), title: text, done: false }]
}

export function toggleTodo(todos, id) {
  return todos.map((t) => (t.id === id ? { ...t, done: !t.done } : t))
}

export function removeTodo(todos, id) {
  return todos.filter((t) => t.id !== id)
}

export function remaining(todos) {
  return todos.filter((t) => !t.done).length
}
