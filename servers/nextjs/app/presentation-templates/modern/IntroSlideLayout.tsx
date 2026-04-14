import React from "react";
import * as z from "zod";
import { ImageSchema } from "../defaultSchemes";

export const layoutId = "intro-pitchdeck-slide";
export const layoutName = "Intro Pitch Deck Slide";
export const layoutDescription =
  "A visually appealing introduction slide for a pitch deck, featuring a large title, company name, date, and contact information with a modern design. This Slide is always the first slide in a pitch deck, setting the tone for the presentation with a clean and professional look.";
const introPitchDeckSchema = z.object({
  title: z.string().min(2).max(15).default("Pitch Deck").meta({
    description: "Main title of the slide",
  }),
  description: z
    .string()
    .min(1)
    .max(200)
    .default("Add a short subtitle or description here. Add a short subtitle or description here. Add a short subtitle or description here. Add a short subtitle or description here.")
    .meta({
      description: "Description shown below the title",
    }),
  image: ImageSchema.default({
    __image_url__:
      "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?q=80&w=1600&auto=format&fit=crop",
    __image_prompt__: "Abstract business background",
  }),


});

export const Schema = introPitchDeckSchema;
export type IntroPitchDeckData = z.infer<typeof introPitchDeckSchema>;

interface IntroSlideLayoutProps {
  data: Partial<IntroPitchDeckData>;
}

const IntroPitchDeckSlide: React.FC<IntroSlideLayoutProps> = ({
  data: slideData,
}) => {

  return (
    <>
      {/* Montserrat Font */}
      <link
        href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
        rel="stylesheet"
      />
      <div
        className="w-full max-w-[1280px] aspect-video mx-auto relative overflow-hidden rounded-md"
        style={{
          fontFamily: "var(--heading-font-family,Montserrat)",
          backgroundColor: 'var(--background-color, #FFFFFF)',
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      >
        {/* Top Header */}
        {((slideData as any)?.__companyName__ || (slideData as any)?._logo_url__) && (
          <div className="absolute top-0 left-0 right-0 px-8 sm:px-12 lg:px-20 pt-4">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1">

                {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
                {(slideData as any)?.__companyName__ && <span className="text-sm sm:text-base font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                  {(slideData as any)?.__companyName__ || 'Company Name'}
                </span>}
              </div>
            </div>
          </div>
        )}

        {/* Main Title and Description (shifted slightly up) */}
        <div
          className="absolute left-10 right-[42%]"
          style={{
            top: "50%",
            transform: "translateY(-50%)",
          }}
        >
          {slideData?.title && (
            <div className="relative inline-block">
              <h1
                className="text-5xl font-bold leading-none"
                style={{ color: 'var(--background-text, #1E4CD9)' }}
                id="pitchdeck-title"
              >
                {slideData?.title}
              </h1>
              <span
                className="block h-[4px] absolute left-0"
                style={{
                  width: "50%",
                  bottom: "-0.5em",
                  transition: "width 0.3s",
                  backgroundColor: 'var(--primary-color, #1E4CD9)'
                }}
              />
            </div>
          )}
          <p className="text-lg leading-relaxed font-normal mt-6 max-w-xl" style={{ color: 'var(--background-text, #234CD9)' }}>
            {slideData?.description}
          </p>
        </div>

        {/* Right Image */}
        {slideData?.image && slideData?.image?.__image_url__ && (
          <div className="absolute top-16 bottom-16 right-10 w-[42%] rounded-md overflow-hidden">
            <img
              src={slideData?.image?.__image_url__}
              alt={slideData?.image?.__image_prompt__ || slideData?.title || "intro-image"}
              className="w-full h-full object-cover"
              crossOrigin="anonymous"
              referrerPolicy="no-referrer"
            />
          </div>
        )}
      </div>
    </>
  );
};

export default IntroPitchDeckSlide;
