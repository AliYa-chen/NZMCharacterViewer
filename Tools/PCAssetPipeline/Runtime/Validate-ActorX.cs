using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using CUE4Parse_Conversion.Dto;

namespace NZMOffline {
    public sealed class ActorXValidation {
        public bool ExactWeightsAndPoints { get; set; }
        public int SourceVertices { get; set; }
        public int WrittenVertices { get; set; }
        public int SourceBones { get; set; }
        public int WrittenBones { get; set; }
        public int SourceWeightRows { get; set; }
        public int WrittenWeightRows { get; set; }
        public int MaximumInfluences { get; set; }
        public int VerticesOverFourInfluences { get; set; }
        public int WrittenTriangles { get; set; }
    }
    public static class ActorXValidator {
        private static void EqualFloat(float actual, float expected, string label) {
            if (BitConverter.SingleToInt32Bits(actual) != BitConverter.SingleToInt32Bits(expected))
                throw new InvalidDataException(label + " does not exactly match the decoded source DTO.");
        }
        public static ActorXValidation Validate(string path, SkeletalMeshDto source) {
            if (source.LODs.Count != 1) throw new InvalidDataException("Validation expects one highest source LOD.");
            var lod = source.LODs[0];
            var result = new ActorXValidation { SourceVertices=lod.Vertices.Length, SourceBones=source.Bones.Length };
            foreach (var vertex in lod.Vertices) {
                int count = vertex.Influences.Length;
                result.SourceWeightRows += count;
                result.MaximumInfluences = Math.Max(result.MaximumInfluences,count);
                if (count>4) result.VerticesOverFourInfluences++;
            }
            bool pointsFound=false, weightsFound=false, bonesFound=false, facesFound=false;
            using var stream = File.OpenRead(path);
            using var reader = new BinaryReader(stream, Encoding.ASCII, true);
            while (stream.Position < stream.Length) {
                if (stream.Length-stream.Position < 32) throw new InvalidDataException("Truncated ActorX chunk header.");
                string name = Encoding.ASCII.GetString(reader.ReadBytes(20)).TrimEnd('\0');
                int type = reader.ReadInt32(), rowSize=reader.ReadInt32(), rowCount=reader.ReadInt32();
                long byteCount=(long)rowSize*rowCount, chunkStart=stream.Position;
                if (rowSize<0 || rowCount<0 || byteCount>stream.Length-chunkStart) throw new InvalidDataException("Invalid ActorX chunk bounds.");
                switch (name) {
                    case "PNTS0000":
                        if (rowSize!=12 || rowCount!=lod.Vertices.Length) throw new InvalidDataException("Point count differs from source.");
                        result.WrittenVertices=rowCount; pointsFound=true;
                        for (int i=0;i<rowCount;i++) {
                            EqualFloat(reader.ReadSingle(),lod.Vertices[i].Position.X,"Point X");
                            EqualFloat(reader.ReadSingle(),-lod.Vertices[i].Position.Y,"Point mirrored Y");
                            EqualFloat(reader.ReadSingle(),lod.Vertices[i].Position.Z,"Point Z");
                        }
                        break;
                    case "RAWWEIGHTS":
                        if (rowSize!=12 || rowCount!=result.SourceWeightRows) throw new InvalidDataException("Weight count differs from source.");
                        result.WrittenWeightRows=rowCount; weightsFound=true;
                        for (int i=0;i<lod.Vertices.Length;i++) {
                            foreach (var influence in lod.Vertices[i].Influences) {
                                float weight=reader.ReadSingle(); int point=reader.ReadInt32(), bone=reader.ReadInt32();
                                EqualFloat(weight,influence.Weight,"Skin weight");
                                if (point!=i || bone!=(int)influence.Bone) throw new InvalidDataException("Skin weight index differs from source.");
                                if (!float.IsFinite(weight) || weight<=0 || weight>1 || bone<0 || bone>=source.Bones.Length)
                                    throw new InvalidDataException("Invalid skin weight/bone.");
                            }
                        }
                        break;
                    case "REFSKELT":
                        result.WrittenBones=rowCount; bonesFound=true;
                        if (rowSize!=120 || rowCount!=source.Bones.Length) throw new InvalidDataException("Bone count differs from source.");
                        break;
                    case "FACE0000":
                    case "FACE3200":
                        result.WrittenTriangles=rowCount; facesFound=true;
                        int expectedFaces=0;foreach(var section in lod.Sections)expectedFaces+=section.NumFaces;
                        if(rowCount!=expectedFaces)throw new InvalidDataException("Face count differs from source sections.");
                        break;
                }
                stream.Position=chunkStart+byteCount;
            }
            if(!pointsFound||!weightsFound||!bonesFound||!facesFound)throw new InvalidDataException("Required ActorX chunk missing.");
            result.ExactWeightsAndPoints=true;
            return result;
        }
    }
}
