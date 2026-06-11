import { v4 as uuidv4 } from 'uuid';

export function createId(prefix = ''): string {
  const id = uuidv4();
  return prefix ? `${prefix}-${id}` : id;
}
