// Runtime check of P12 responses against the generated contract (same subset as Python).
import { COMPONENTS } from './contract.generated.js'

export class ContractError extends Error {
  constructor (path, detail) {
    super(`${path}: ${detail}`)
    this.reasonCode = 'SCHEMA_VERSION_MISMATCH'
    this.path = path
  }
}

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/

function check (schema, value, path) {
  if (schema.$ref) return check(COMPONENTS[schema.$ref.split('/').pop()], value, path)
  if (schema.anyOf) {
    const failures = []
    for (const option of schema.anyOf) {
      try { check(option, value, path); return } catch (error) { failures.push(error.message) }
    }
    throw new ContractError(path, `no admitted alternative (${failures.join('; ')})`)
  }
  if ('const' in schema && value !== schema.const) throw new ContractError(path, `must equal ${schema.const}`)
  switch (schema.type) {
    case 'null':
      if (value !== null) throw new ContractError(path, 'must be null')
      return
    case 'string':
      if (typeof value !== 'string') throw new ContractError(path, 'must be a string')
      if (schema.enum && !schema.enum.includes(value)) throw new ContractError(path, `${value} is not admitted`)
      if (schema.pattern && !new RegExp(schema.pattern).test(value)) throw new ContractError(path, 'bad format')
      if (schema.minLength && value.length < schema.minLength) throw new ContractError(path, 'too short')
      if (schema.maxLength && value.length > schema.maxLength) throw new ContractError(path, 'too long')
      if (schema.format === 'date-time' && !ISO.test(value)) throw new ContractError(path, 'must be an aware ISO timestamp')
      return
    case 'integer':
      if (!Number.isInteger(value)) throw new ContractError(path, 'must be an integer')
      if ('minimum' in schema && value < schema.minimum) throw new ContractError(path, 'below minimum')
      return
    case 'boolean':
      if (typeof value !== 'boolean') throw new ContractError(path, 'must be a boolean')
      return
    case 'array':
      if (!Array.isArray(value)) throw new ContractError(path, 'must be an array')
      value.forEach((item, index) => check(schema.items, item, `${path}[${index}]`))
      return
    case 'object': {
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ContractError(path, 'must be an object')
      const keys = Object.keys(value).sort()
      const required = [...schema.required].sort()
      if (keys.join(',') !== required.join(',')) throw new ContractError(path, `fields differ: ${keys.join(',')}`)
      for (const key of required) check(schema.properties[key], value[key], `${path}.${key}`)
      return
    }
    default:
      if (schema.enum && !schema.enum.includes(value)) throw new ContractError(path, 'not admitted')
  }
}

export function validate (component, value) {
  check(COMPONENTS[component], value, component)
  return value
}
