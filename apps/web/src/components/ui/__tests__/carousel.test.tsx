import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Carousel, CarouselNext, CarouselPrevious } from '@/components/ui/carousel';

type ScrollListener = () => void;

const embla = vi.hoisted(() => {
  const listeners = new Set<ScrollListener>();
  let canScrollPrev = false;

  const api = {
    on: (_event: string, listener: ScrollListener): void => {
      listeners.add(listener);
    },
    off: (_event: string, listener: ScrollListener): void => {
      listeners.delete(listener);
    },
    canScrollPrev: (): boolean => canScrollPrev,
    canScrollNext: (): boolean => true,
    scrollPrev: (): void => undefined,
    scrollNext: (): void => undefined,
    scrollTo: (): void => {
      canScrollPrev = true;
      for (const listener of listeners) {
        listener();
      }
    },
  };

  return {
    api,
    reset: (): void => {
      canScrollPrev = false;
      listeners.clear();
    },
  };
});

vi.mock('embla-carousel-react', () => ({
  default: () => [() => undefined, embla.api],
}));

describe('Carousel scroll flags', () => {
  afterEach(() => {
    cleanup();
    embla.reset();
  });

  it('keeps previous disabled when Embla cannot scroll back', () => {
    render(
      <Carousel>
        <CarouselPrevious />
        <CarouselNext />
      </Carousel>
    );

    expect(screen.getByRole('button', { name: 'Previous slide' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Next slide' })).toBeEnabled();
  });

  it('enables previous when setApi scrolls before select is subscribed', () => {
    render(
      <Carousel
        setApi={(carouselApi) => {
          carouselApi?.scrollTo(1);
        }}
      >
        <CarouselPrevious />
      </Carousel>
    );

    expect(screen.getByRole('button', { name: 'Previous slide' })).toBeEnabled();
  });
});
