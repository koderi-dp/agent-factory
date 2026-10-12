import { test } from 'node:test'
import assert from 'node:assert/strict'
import { addTodo, toggleTodo, removeTodo, remaining } from '../src/todos.js'

test('addTodo trims and ignores blank titles', () => {
  assert.deepEqual(addTodo([], '   '), [])
  const [t] = addTodo([], '  milk ')
  assert.equal(t.title, 'milk')
  assert.equal(t.done, false)
})

test('toggleTodo flips one todo', () => {
  const todos = [{ id: 'a', title: 'a', done: false }, { id: 'b', title: 'b', done: false }]
  assert.deepEqual(toggleTodo(todos, 'a').map((t) => t.done), [true, false])
})

test('removeTodo removes by id', () => {
  const todos = [{ id: 'a', title: 'a', done: false }, { id: 'b', title: 'b', done: true }]
  assert.deepEqual(removeTodo(todos, 'a').map((t) => t.id), ['b'])
})

test('remaining counts open todos', () => {
  assert.equal(remaining([{ id: 'a', done: true }, { id: 'b', done: false }]), 1)
})
