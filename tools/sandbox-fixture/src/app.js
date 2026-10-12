import { addTodo, toggleTodo, removeTodo, remaining } from './todos.js'

const KEY = 'todos'
let todos = JSON.parse(localStorage.getItem(KEY) || '[]')

const list = document.getElementById('list')
const empty = document.getElementById('empty')
const summary = document.getElementById('summary')

function save() {
  localStorage.setItem(KEY, JSON.stringify(todos))
}

function render() {
  list.innerHTML = ''
  for (const t of todos) {
    const li = document.createElement('li')
    li.className = t.done ? 'item done' : 'item'

    const box = document.createElement('input')
    box.type = 'checkbox'
    box.checked = t.done
    box.id = `todo-${t.id}`
    box.addEventListener('change', () => update(toggleTodo(todos, t.id)))

    const label = document.createElement('label')
    label.htmlFor = box.id
    label.textContent = t.title

    const del = document.createElement('button')
    del.className = 'remove'
    del.textContent = 'Delete'
    del.addEventListener('click', () => update(removeTodo(todos, t.id)))

    li.append(box, label, del)
    list.append(li)
  }
  empty.hidden = todos.length > 0
  const left = remaining(todos)
  summary.textContent = todos.length ? `${left} ${left === 1 ? 'item' : 'items'} left` : ''
}

function update(next) {
  todos = next
  save()
  render()
}

document.getElementById('new-todo').addEventListener('submit', (e) => {
  e.preventDefault()
  const input = e.target.elements.title
  update(addTodo(todos, input.value))
  input.value = ''
  input.focus()
})

render()
