import type { HttpClient } from './http'

export type LocationPhoto = { id: string; description: string | null; file_format: 'jpeg' | 'png' | 'webp'; byte_size: number; width_px: number; height_px: number; created_at: string }
export type LocationDetails = { address: string | null; access_instructions: string | null; shooting_constraints: string | null }
export type LocationGallery = { resource_id: string; resource_name: string; resource_status: string; resource_lock_version: number; details: LocationDetails;
  photos: LocationPhoto[]; allowed_actions: ('edit_details' | 'upload_photo' | 'edit_photo' | 'remove_photo')[] }
export type LocationCommand = { action: 'edit_details' | 'edit_photo' | 'remove_photo'; photoId?: string; body: Record<string, unknown>; key: string }

export function createLocationClient(http: HttpClient) {
  const path = (crew: string, id: string) => `/crews/${crew}/locations/${id}`
  return {
    location: (crew: string, id: string, signal?: AbortSignal) => http.request(path(crew, id), { signal }) as Promise<LocationGallery>,
    uploadPhoto: (crew: string, id: string, file: File, description: string | null, version: number, key: string) => {
      const form = new FormData()
      form.append('file', file); form.append('expected_lock_version', String(version))
      if (description !== null) form.append('description', description)
      return http.upload(`${path(crew, id)}/photos`, form, key) as Promise<LocationGallery>
    },
    locationWrite: (crew: string, id: string, command: LocationCommand) => http.mutate(`${path(crew, id)}${command.action === 'edit_details' ? '' : `/photos/${command.photoId}${command.action === 'remove_photo' ? '/remove' : ''}`}`,
      command.body, command.action === 'remove_photo' ? 'POST' : 'PATCH', [200], command.key) as Promise<LocationGallery>,
  }
}

export const locationPhotoUrl = (crew: string, resource: string, photo: string, variant: 'preview' | 'full' = 'preview') => `/api/v1/crews/${crew}/locations/${resource}/photos/${photo}/content?variant=${variant}`
